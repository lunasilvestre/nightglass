package bundle

import (
	"archive/tar"
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/lunasilvestre/nightglass/bundler/internal/dockercli"
	"github.com/lunasilvestre/nightglass/bundler/internal/manifest"
	"github.com/lunasilvestre/nightglass/bundler/internal/sources"
)

// CreateSpec is everything create needs to know.
type CreateSpec struct {
	Repo         string   // the clone to read data/ and data/sources.yaml from
	Out          string   // where the tarball goes
	Staging      string   // scratch for docker save and the wheelhouse
	Images       []string // refs, in load order
	ModelsVolume string
	HelperImage  string // the image used to read the volume, and carried in the bundle
	PythonImage  string // the image the wheelhouse is resolved against
	All          bool   // include role: optional and superseded
	SkipWheels   bool
	Created      string // RFC3339; pinned makes a rebuild byte-identical
	Tool         string
	Out2         io.Writer // progress
	TTY          bool
}

// item is one file on its way into the bundle.
//
// expect is a digest the file MUST have, from somewhere other than the file
// itself — data/sources.yaml for a granule, the blob's own filename for a model
// blob. It is the difference between a bundle that records what it found and a
// bundle that checks what it found against what was declared.
type item struct {
	src    string
	path   string
	kind   manifest.Kind
	image  *manifest.Image
	role   string
	note   string
	expect string
	why    string
}

// Create builds a bundle.
//
// It is a two-pass writer, and it has to be: the manifest is the first member
// and it contains a digest of every member after it, so nothing can be written
// until everything has been read. Create runs once per bundle and verify runs
// every time one is moved, so the cost belongs on this side.
func Create(spec CreateSpec) (*manifest.Manifest, string, error) {
	log := spec.Out2
	if log == nil {
		log = io.Discard
	}
	if spec.Created == "" {
		spec.Created = time.Now().UTC().Format(time.RFC3339)
	}
	created, err := time.Parse(time.RFC3339, spec.Created)
	if err != nil {
		return nil, "", fmt.Errorf("--created %q is not RFC3339: %w", spec.Created, err)
	}

	if err := dockercli.Available(); err != nil {
		return nil, "", err
	}
	if err := os.MkdirAll(spec.Staging, 0o755); err != nil {
		return nil, "", err
	}

	var items []item

	// -- images ------------------------------------------------------------
	fmt.Fprintf(log, "\n\033[1mimages\033[0m\n%s\n", strings.Repeat("-", 60))
	imgDir := filepath.Join(spec.Staging, "images")
	if err := os.MkdirAll(imgDir, 0o755); err != nil {
		return nil, "", err
	}
	for _, ref := range spec.Images {
		info, err := dockercli.Inspect(ref)
		if err != nil {
			return nil, "", err
		}
		name := refFile(ref)
		dst := filepath.Join(imgDir, name)
		fmt.Fprintf(log, "   docker save %-38s", ref)
		if err := dockercli.Save(ref, dst); err != nil {
			return nil, "", err
		}
		st, err := os.Stat(dst)
		if err != nil {
			return nil, "", err
		}
		fmt.Fprintf(log, "%14s\n", commas(st.Size()))
		items = append(items, item{
			src: dst, path: "images/" + name, kind: manifest.KindImage,
			image: &manifest.Image{Ref: info.Ref, ID: info.ID, RepoDigest: info.RepoDigest},
		})
	}

	// -- model blobs -------------------------------------------------------
	fmt.Fprintf(log, "\n\033[1mmodels\033[0m\n%s\n", strings.Repeat("-", 60))
	modelItems, err := stageModels(spec, log)
	if err != nil {
		return nil, "", err
	}
	items = append(items, modelItems...)

	// -- wheels ------------------------------------------------------------
	if !spec.SkipWheels {
		fmt.Fprintf(log, "\n\033[1mwheels\033[0m\n%s\n", strings.Repeat("-", 60))
		wheelItems, err := stageWheels(spec, log)
		if err != nil {
			return nil, "", err
		}
		items = append(items, wheelItems...)
	}

	// -- data, cross-checked against the committed manifest ----------------
	fmt.Fprintf(log, "\n\033[1mdata\033[0m\n%s\n", strings.Repeat("-", 60))
	dataItems, err := gatherData(spec, log)
	if err != nil {
		return nil, "", err
	}
	items = append(items, dataItems...)

	// -- pass 1: hash everything -------------------------------------------
	fmt.Fprintf(log, "\n\033[1mhashing\033[0m\n%s\n", strings.Repeat("-", 60))
	entries, err := hashAll(items, log, spec.TTY)
	if err != nil {
		return nil, "", err
	}

	m := manifest.New(spec.Tool, spec.Created, manifest.Source{
		GitCommit: gitCommit(spec.Repo),
		GitDirty:  gitDirty(spec.Repo),
		Docker:    dockercli.ServerVersion(),
	}, entries)
	if err := m.Validate(); err != nil {
		return nil, "", err
	}
	raw, err := m.Marshal()
	if err != nil {
		return nil, "", err
	}

	// -- pass 2: write the archive, manifest first -------------------------
	fmt.Fprintf(log, "\n\033[1mwriting\033[0m %s\n%s\n", spec.Out, strings.Repeat("-", 60))
	if err := writeArchive(spec.Out, raw, entries, created, log, spec.TTY); err != nil {
		return nil, "", err
	}

	sum := sha256.Sum256(raw)
	digest := hex.EncodeToString(sum[:])
	if err := os.WriteFile(spec.Out+".sha256",
		[]byte(digest+"  "+manifest.Name+"\n"), 0o644); err != nil {
		return nil, "", err
	}
	return m, digest, nil
}

// stageModels copies the ollama model store out of its named volume.
//
// Only models/ travels. The volume also holds id_ed25519 — an OpenSSH PRIVATE
// key, mode 600, which is the instance's identity. Bundling it would ship one
// private key to every site that restores this bundle, and a fresh Ollama
// generates its own on first run, so carrying it costs the property that two
// deployments are distinct and buys nothing. cache/model-recommendations.json
// is the residue of finding 14's ollama.com call: a cache, and a trace of the
// one outbound path the enclave exists to prevent.
func stageModels(spec CreateSpec, log io.Writer) ([]item, error) {
	if !dockercli.VolumeExists(spec.ModelsVolume) {
		return nil, fmt.Errorf(
			"no docker volume %q.\n"+
				"  The model blobs are 56%% of a bundle and there is nothing to carry\n"+
				"  without them. Run `make pull-models` (or `make seed-models`) first.",
			spec.ModelsVolume)
	}
	dest := filepath.Join(spec.Staging, "models")
	if err := os.RemoveAll(dest); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(dest, 0o755); err != nil {
		return nil, err
	}

	r, wait, err := dockercli.TarFromVolume(spec.ModelsVolume, spec.HelperImage, "models")
	if err != nil {
		return nil, err
	}
	defer r.Close()

	var items []item
	tr := tar.NewReader(r)
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("reading %s: %w", spec.ModelsVolume, err)
		}
		if hdr.Typeflag != tar.TypeReg {
			continue
		}
		// "models/blobs/sha256-…" and "models/manifests/registry.ollama.ai/…"
		rel := filepath.Clean(hdr.Name)
		out := filepath.Join(dest, strings.TrimPrefix(rel, "models/"))
		if err := os.MkdirAll(filepath.Dir(out), 0o755); err != nil {
			return nil, err
		}
		f, err := os.Create(out)
		if err != nil {
			return nil, err
		}
		if _, err := io.CopyBuffer(f, tr, make([]byte, copyBuf)); err != nil {
			f.Close()
			return nil, err
		}
		if err := f.Close(); err != nil {
			return nil, err
		}

		it := item{src: out, path: "models/" + strings.TrimPrefix(rel, "models/")}
		base := filepath.Base(rel)
		if strings.HasPrefix(base, "sha256-") {
			it.kind = manifest.KindModelBlob
			// The blob's filename IS its checksum. Requiring the content to
			// match it means create verifies the store against itself before
			// it carries it — the "right size, wrong bytes" case caught at the
			// only moment it can still be fixed cheaply.
			it.expect = strings.TrimPrefix(base, "sha256-")
			it.why = "the blob's own filename"
		} else {
			it.kind = manifest.KindModelManifest
		}
		items = append(items, it)
	}
	if err := wait(); err != nil {
		return nil, err
	}

	sort.Slice(items, func(i, j int) bool { return items[i].path < items[j].path })
	var total int64
	for _, it := range items {
		if st, err := os.Stat(it.src); err == nil {
			total += st.Size()
		}
	}
	fmt.Fprintf(log, "   %-38s %3d files %14s\n", spec.ModelsVolume+" models/", len(items), commas(total))
	fmt.Fprintf(log, "   %-38s %s\n", "excluded", "id_ed25519, id_ed25519.pub, cache/")
	return items, nil
}

// stageWheels resolves the wheelhouse from the image that will run them.
//
// `pip freeze` inside the runtime image rather than a resolve from
// pyproject.toml, because the question a bundle answers is "what is installed
// in the thing being shipped", not "what would a fresh resolve pick today".
func stageWheels(spec CreateSpec, log io.Writer) ([]item, error) {
	dir := filepath.Join(spec.Staging, "wheels")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}

	frozen, err := dockercli.Run("run", "--rm", "--entrypoint", "pip", spec.PythonImage,
		"freeze", "--exclude-editable")
	if err != nil {
		return nil, fmt.Errorf("resolving the installed set from %s:\n  %w", spec.PythonImage, err)
	}
	req := filepath.Join(dir, "requirements.txt")
	if err := os.WriteFile(req, []byte(frozen), 0o644); err != nil {
		return nil, err
	}
	n := len(strings.Fields(frozen))
	fmt.Fprintf(log, "   %-38s %3d packages\n", "pip freeze "+spec.PythonImage, n)

	// The container runs as uid 10001 and the staging directory belongs to the
	// invoking user, so pip cannot create its working directories. --user with
	// the host uid is the same fix docker-compose.yml applies to every fetcher
	// that writes onto a bind mount; HOME has to move too, or pip looks for a
	// cache under a home directory that does not exist for that uid.
	fmt.Fprintf(log, "   %-38s", "pip download")
	if _, err := dockercli.Run("run", "--rm",
		"--user", fmt.Sprintf("%d:%d", os.Getuid(), os.Getgid()),
		"-e", "HOME=/out",
		"-v", dir+":/out",
		"--entrypoint", "pip", spec.PythonImage,
		"download", "-r", "/out/requirements.txt", "-d", "/out",
		"--only-binary=:all:"); err != nil {
		return nil, fmt.Errorf("building the wheelhouse:\n  %w", err)
	}

	names, err := filepath.Glob(filepath.Join(dir, "*.whl"))
	if err != nil {
		return nil, err
	}
	sort.Strings(names)
	items := []item{{src: req, path: "wheels/requirements.txt", kind: manifest.KindWheel}}
	var total int64
	for _, p := range names {
		if st, err := os.Stat(p); err == nil {
			total += st.Size()
		}
		items = append(items, item{
			src: p, path: "wheels/" + filepath.Base(p), kind: manifest.KindWheel,
		})
	}
	fmt.Fprintf(log, "%3d wheels %14s\n", len(names), commas(total))
	return items, nil
}

// gatherData collects the granules and the AIS day, and requires each to match
// data/sources.yaml. Files are referenced where they lie; nothing is copied.
func gatherData(spec CreateSpec, log io.Writer) ([]item, error) {
	manifestPath := filepath.Join(spec.Repo, "data", "sources.yaml")
	src, err := sources.Load(manifestPath)
	if err != nil {
		return nil, err
	}
	wanted, skipped := src.Select(spec.All)

	var items []item
	for _, it := range wanted {
		sub := "sar"
		if it.Section == "ais" {
			sub = "ais"
		}
		host := filepath.Join(spec.Repo, "data", "raw", sub, it.Name)
		st, err := os.Stat(host)
		if err != nil {
			return nil, fmt.Errorf(
				"%s is in data/sources.yaml with role %s, and is not on disk.\n"+
					"  Run `make fetch-granules` and `make fetch-ais` before bundling.\n"+
					"  A bundle exists so a site that cannot reach ASF or the DMA still has\n"+
					"  these bytes; it cannot be built without them.", it.Name, it.Role)
		}
		if st.Size() != it.Bytes {
			return nil, fmt.Errorf(
				"%s is %s bytes on disk, data/sources.yaml says %s.\n"+
					"  A truncated fetch looks exactly like this. Delete it and re-fetch.",
				it.Name, commas(st.Size()), commas(it.Bytes))
		}
		items = append(items, item{
			src: host, path: "data/raw/" + sub + "/" + it.Name, kind: manifest.KindData,
			role: string(it.Role), note: it.Note,
			expect: it.SHA256, why: "data/sources.yaml",
		})
		fmt.Fprintf(log, "   %-46s %14s  %s\n", short(it.Name), commas(it.Bytes), it.Role)
	}
	// Both halves are reported. "Three of six granules were bundled" and "three
	// granules were bundled" are different claims and the fetchers already make
	// the first one.
	for _, it := range skipped {
		fmt.Fprintf(log, "   %-46s %14s  %s (skipped; --all includes it)\n",
			short(it.Name), commas(it.Bytes), it.Role)
	}
	return items, nil
}

// hashAll is the single place a digest is computed at create time.
func hashAll(items []item, log io.Writer, tty bool) ([]manifest.Entry, error) {
	entries := make([]manifest.Entry, 0, len(items))
	var done int64
	for _, it := range items {
		st, err := os.Stat(it.src)
		if err != nil {
			return nil, err
		}
		sum, err := sha256File(it.src)
		if err != nil {
			return nil, err
		}
		if it.expect != "" && sum != it.expect {
			return nil, fmt.Errorf(
				"%s does not match %s.\n"+
					"    on disk  %s\n"+
					"    declared %s\n"+
					"  These are not the bytes this deployment's numbers were measured over.\n"+
					"  Refusing to bundle them under a checksum of their own making.",
				filepath.Base(it.src), it.why, sum, it.expect)
		}
		entries = append(entries, manifest.Entry{
			Path: it.path, Bytes: st.Size(), SHA256: sum, Kind: it.kind,
			Image: it.image, Role: it.role, Note: it.note, Src: it.src,
		})
		done += st.Size()
		if tty {
			fmt.Fprintf(log, "\r   %-46s %14s", short(it.path), commas(done))
		}
	}
	if tty {
		fmt.Fprintf(log, "\r%s\r", strings.Repeat(" ", 64))
	}
	fmt.Fprintf(log, "   %d entries, %s bytes\n", len(entries), commas(done))
	return entries, nil
}

// writeArchive lays the tar out: the manifest, then every member in manifest
// order. Modification times all come from Created, so pinning --created makes
// two bundles of identical inputs byte-identical.
func writeArchive(out string, raw []byte, entries []manifest.Entry,
	created time.Time, log io.Writer, tty bool) error {

	if err := freeSpace(filepath.Dir(out), totalOf(entries)+int64(len(raw))); err != nil {
		return err
	}
	part := out + ".part"
	f, err := os.Create(part)
	if err != nil {
		return err
	}
	bw := bufio.NewWriterSize(f, copyBuf)
	tw := tar.NewWriter(bw)

	put := func(name string, size int64, r io.Reader) error {
		if err := tw.WriteHeader(&tar.Header{
			Name: name, Mode: 0o644, Size: size,
			ModTime: created, Typeflag: tar.TypeReg,
			Format: tar.FormatPAX,
		}); err != nil {
			return err
		}
		_, err := io.CopyBuffer(tw, r, make([]byte, copyBuf))
		return err
	}

	fail := func(err error) error {
		f.Close()
		os.Remove(part)
		return err
	}

	if err := put(manifest.Name, int64(len(raw)), bytes.NewReader(raw)); err != nil {
		return fail(err)
	}
	var done int64
	for _, e := range entries {
		src, err := os.Open(e.Src)
		if err != nil {
			return fail(err)
		}
		err = put(e.Path, e.Bytes, src)
		src.Close()
		if err != nil {
			return fail(err)
		}
		done += e.Bytes
		if tty {
			fmt.Fprintf(log, "\r   %-46s %14s", short(e.Path), commas(done))
		}
	}
	if tty {
		fmt.Fprintf(log, "\r%s\r", strings.Repeat(" ", 64))
	}
	if err := tw.Close(); err != nil {
		return fail(err)
	}
	if err := bw.Flush(); err != nil {
		return fail(err)
	}
	if err := f.Sync(); err != nil {
		return fail(err)
	}
	if err := f.Close(); err != nil {
		os.Remove(part)
		return err
	}
	// Rename last, so an interrupted create never leaves something that looks
	// like a finished bundle. The same discipline archive.py uses for a
	// download, for the same reason.
	return os.Rename(part, out)
}

func totalOf(entries []manifest.Entry) int64 {
	var n int64
	for _, e := range entries {
		n += e.Bytes
	}
	return n
}

func sha256File(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.CopyBuffer(h, f, make([]byte, copyBuf)); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// freeSpace refuses before writing rather than after filling a disk.
func freeSpace(dir string, need int64) error {
	var st syscall.Statfs_t
	if err := syscall.Statfs(dir, &st); err != nil {
		return nil // not a reason to stop; the write will report its own failure
	}
	avail := int64(st.Bavail) * int64(st.Bsize)
	if avail < need {
		return fmt.Errorf(
			"%s has %s free and this bundle needs %s.\n"+
				"  Pass --out somewhere with room. Note that `make clean` would free space\n"+
				"  by destroying the model volume this bundle is mostly made of.",
			dir, commas(avail), commas(need))
	}
	return nil
}

// refFile turns an image reference into a filename that survives a filesystem.
func refFile(ref string) string {
	r := strings.NewReplacer("/", "_", ":", "_", "@", "_")
	return r.Replace(ref) + ".tar"
}

func gitCommit(repo string) string {
	out, err := exec.Command("git", "-C", repo, "rev-parse", "--short", "HEAD").Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

func gitDirty(repo string) bool {
	out, err := exec.Command("git", "-C", repo, "status", "--porcelain").Output()
	if err != nil {
		return false
	}
	return strings.TrimSpace(string(out)) != ""
}
