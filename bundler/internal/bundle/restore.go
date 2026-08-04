package bundle

import (
	"archive/tar"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/lunasilvestre/nightglass/bundler/internal/dockercli"
	"github.com/lunasilvestre/nightglass/bundler/internal/manifest"
)

// RestoreSpec is everything restore needs to know.
type RestoreSpec struct {
	Into         string // where the archive is unpacked
	Repo         string // the clone data/ is placed into; required with Install
	ModelsVolume string
	HelperImage  string
	Install      bool // drive docker as well as writing files
	Opt          Options
	Log          io.Writer
}

// Restore unpacks a bundle, and optionally installs it.
//
// The unpack is transactional. Every member is written to <path>.part while it
// is hashed, and NOTHING is renamed until the entire archive has passed all six
// checks — including the one that catches a member the manifest lists and the
// archive does not contain. A half-restored bundle is a worse state than an
// unrestored one: the images would load, the system would start, and the thing
// that was missing would be discovered by whatever needed it first.
//
// The same discipline, for the same reason, as the .part files in
// src/nightglass/spatial/archive.py: a file that is the right size and the
// wrong bytes must never be mistaken for a finished one.
func Restore(r io.Reader, spec RestoreSpec) (*Result, error) {
	log := spec.Log
	if log == nil {
		log = io.Discard
	}
	if spec.Into == "" {
		return nil, fmt.Errorf("--into is required: restore writes files somewhere")
	}
	if spec.Install {
		if err := looksLikeClone(spec.Repo); err != nil {
			return nil, err
		}
		if err := dockercli.Available(); err != nil {
			return nil, err
		}
	}
	if err := os.MkdirAll(spec.Into, 0o755); err != nil {
		return nil, err
	}

	var parts []string
	sink := func(e *manifest.Entry) (io.WriteCloser, error) {
		dst := filepath.Join(spec.Into, filepath.FromSlash(e.Path))
		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
			return nil, err
		}
		f, err := os.Create(dst + ".part")
		if err != nil {
			return nil, err
		}
		parts = append(parts, dst)
		return f, nil
	}

	res, err := walk(r, spec.Opt, sink)
	if err != nil {
		// Leave the .part files where they are. They are what someone would
		// look at to work out what arrived, and nothing downstream will mistake
		// them for restored files.
		return nil, err
	}

	// Only now.
	for _, dst := range parts {
		if err := os.Rename(dst+".part", dst); err != nil {
			return nil, err
		}
	}
	fmt.Fprintf(log, "\n   unpacked %d entries, %s bytes into %s\n",
		res.Entries, commas(res.Bytes), spec.Into)

	if !spec.Install {
		printNextSteps(log, spec, res)
		return res, nil
	}
	if err := install(spec, res, log); err != nil {
		return nil, err
	}
	return res, nil
}

func install(spec RestoreSpec, res *Result, log io.Writer) error {
	// -- images ------------------------------------------------------------
	//
	// First, because the helper image that writes the model volume is one of
	// them. A bundle that needed a tool it did not carry would fail here on the
	// one kind of host it exists for.
	fmt.Fprintf(log, "\n\033[1mdocker load\033[0m\n%s\n", strings.Repeat("-", 60))
	for _, e := range res.Manifest.Entries {
		if e.Kind != manifest.KindImage {
			continue
		}
		out, err := dockercli.Load(filepath.Join(spec.Into, filepath.FromSlash(e.Path)))
		if err != nil {
			return err
		}
		fmt.Fprintf(log, "   %-46s %s\n", e.Image.Ref, firstLine(out))
	}

	// -- model blobs -------------------------------------------------------
	models := filepath.Join(spec.Into, "models")
	if st, err := os.Stat(models); err == nil && st.IsDir() {
		fmt.Fprintf(log, "\n\033[1mmodel volume\033[0m %s\n%s\n",
			spec.ModelsVolume, strings.Repeat("-", 60))
		n, bytes, err := fillVolume(spec, models)
		if err != nil {
			return err
		}
		fmt.Fprintf(log, "   %-46s %3d files %s\n", "models/", n, commas(bytes))
		fmt.Fprintf(log, "   %-46s %s\n", "not carried",
			"id_ed25519 — a fresh ollama generates its own")
	}

	// -- data --------------------------------------------------------------
	if err := placeData(spec, res, log); err != nil {
		return err
	}

	fmt.Fprintf(log, "\n   Restored. `make up` starts the enclave; the databases are not in the\n")
	fmt.Fprintf(log, "   bundle, so a fresh site still runs migrate, scenes, detect and ingest.\n")
	return nil
}

// fillVolume streams the unpacked model tree into the named volume through the
// helper image, which is the only portable way to write a volume: it lives
// under the daemon's storage root and is owned by root.
func fillVolume(spec RestoreSpec, models string) (int, int64, error) {
	var count int
	var total int64
	pr, pw := io.Pipe()

	go func() {
		tw := tar.NewWriter(pw)
		err := filepath.Walk(models, func(p string, info os.FileInfo, err error) error {
			if err != nil {
				return err
			}
			if info.IsDir() {
				return nil
			}
			rel, err := filepath.Rel(models, p)
			if err != nil {
				return err
			}
			hdr := &tar.Header{
				Name:     "models/" + filepath.ToSlash(rel),
				Mode:     0o644,
				Size:     info.Size(),
				ModTime:  info.ModTime(),
				Typeflag: tar.TypeReg,
				Format:   tar.FormatPAX,
			}
			if err := tw.WriteHeader(hdr); err != nil {
				return err
			}
			f, err := os.Open(p)
			if err != nil {
				return err
			}
			defer f.Close()
			if _, err := io.CopyBuffer(tw, f, make([]byte, copyBuf)); err != nil {
				return err
			}
			count++
			total += info.Size()
			return nil
		})
		if err != nil {
			pw.CloseWithError(err)
			return
		}
		if err := tw.Close(); err != nil {
			pw.CloseWithError(err)
			return
		}
		pw.Close()
	}()

	err := dockercli.TarIntoVolume(spec.ModelsVolume, spec.HelperImage, pr)
	return count, total, err
}

// placeData moves the granules and the AIS day into the clone, where the
// enclave's read-only mounts expect them.
func placeData(spec RestoreSpec, res *Result, log io.Writer) error {
	var moved int
	var total int64
	for _, e := range res.Manifest.Entries {
		if e.Kind != manifest.KindData {
			continue
		}
		src := filepath.Join(spec.Into, filepath.FromSlash(e.Path))
		dst := filepath.Join(spec.Repo, filepath.FromSlash(e.Path))
		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
			return err
		}
		if err := os.Rename(src, dst); err != nil {
			// Across filesystems rename fails; fall back to a copy.
			if err := copyFile(src, dst); err != nil {
				return err
			}
			os.Remove(src)
		}
		moved++
		total += e.Bytes
	}
	if moved > 0 {
		fmt.Fprintf(log, "\n\033[1mdata\033[0m\n%s\n", strings.Repeat("-", 60))
		fmt.Fprintf(log, "   %-46s %3d files %s\n",
			filepath.Join(spec.Repo, "data/raw"), moved, commas(total))
	}
	return nil
}

func printNextSteps(log io.Writer, spec RestoreSpec, res *Result) {
	var images []string
	for _, e := range res.Manifest.Entries {
		if e.Kind == manifest.KindImage {
			images = append(images, e.Image.Ref)
		}
	}
	sort.Strings(images)
	fmt.Fprintf(log, "\n   Verified and unpacked, nothing installed. Pass --install to have this\n")
	fmt.Fprintf(log, "   tool do the rest, or do it by hand — the bundle is a plain tar and\n")
	fmt.Fprintf(log, "   every step below is a command you can read:\n\n")
	for _, ref := range images {
		fmt.Fprintf(log, "     docker load < %s\n",
			filepath.Join(spec.Into, "images", refFile(ref)))
	}
	fmt.Fprintf(log, "     tar -C %s -cf - models | \\\n", spec.Into)
	fmt.Fprintf(log, "       docker run --rm -i -v %s:/dest %s tar -C /dest -xf -\n",
		spec.ModelsVolume, spec.HelperImage)
	fmt.Fprintf(log, "     mv %s/data/raw/* <clone>/data/raw/\n", spec.Into)
}

// looksLikeClone refuses to scatter 3.65 GB of granules into a directory that
// is not a nightglass checkout.
func looksLikeClone(repo string) error {
	if repo == "" {
		return fmt.Errorf("--install needs --repo: the data half is placed inside a clone")
	}
	compose := filepath.Join(repo, "docker-compose.yml")
	raw, err := os.ReadFile(compose)
	if err != nil {
		return fmt.Errorf(
			"%s does not look like a nightglass clone (no docker-compose.yml).\n"+
				"  Restoring the data half into an arbitrary directory is not something to\n"+
				"  do by accident.", repo)
	}
	if !strings.Contains(string(raw), "name: nightglass") {
		return fmt.Errorf("%s has a docker-compose.yml, but it is not nightglass's", repo)
	}
	return nil
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	if _, err := io.CopyBuffer(out, in, make([]byte, copyBuf)); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}

func firstLine(s string) string {
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		return s[:i]
	}
	return s
}
