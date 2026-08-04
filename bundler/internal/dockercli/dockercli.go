// Package dockercli drives the docker CLI.
//
// It shells out rather than linking the Docker client library, and that is a
// deliberate trade rather than laziness. The argument for this tool being Go is
// that it is a single static binary with no interpreter and no shared
// libraries behind it — the thing that unpacks an air-gapped bundle cannot
// itself need an environment to exist first. Linking the client library would
// pull a large dependency tree in order to re-implement four commands that are
// already present on any host capable of restoring the bundle at all.
//
// Everything here runs at create time or at restore time. Neither is inside the
// enclave.
package dockercli

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
)

// Err carries the command's stderr, because docker's own diagnosis is almost
// always better than any wrapper's guess at it.
type Err struct {
	Args   []string
	Stderr string
	Err    error
}

func (e *Err) Error() string {
	msg := strings.TrimSpace(e.Stderr)
	if msg == "" {
		msg = e.Err.Error()
	}
	return fmt.Sprintf("docker %s: %s", strings.Join(e.Args, " "), msg)
}

func (e *Err) Unwrap() error { return e.Err }

// Run executes a docker command and returns its stdout.
func Run(args ...string) (string, error) {
	cmd := exec.Command("docker", args...)
	var out, errb bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &errb
	if err := cmd.Run(); err != nil {
		return "", &Err{Args: args, Stderr: errb.String(), Err: err}
	}
	return out.String(), nil
}

// Available reports whether a usable docker is on PATH and its daemon answers.
func Available() error {
	if _, err := exec.LookPath("docker"); err != nil {
		return fmt.Errorf("no `docker` on PATH. This tool drives it for `save` and `load`")
	}
	if _, err := Run("version", "--format", "{{.Server.Version}}"); err != nil {
		return fmt.Errorf("docker is installed but its daemon did not answer:\n  %w", err)
	}
	return nil
}

// ServerVersion is recorded in the manifest so a restored site can tell which
// daemon wrote the image archives.
func ServerVersion() string {
	v, err := Run("version", "--format", "{{.Server.Version}}")
	if err != nil {
		return ""
	}
	return strings.TrimSpace(v)
}

// ImageInfo is what the manifest records about an image.
type ImageInfo struct {
	Ref        string
	ID         string
	RepoDigest string
}

// Inspect resolves a tag to its local content digest and, where one exists, to
// a registry-resolvable digest.
//
// nightglass/app:dev and nightglass/fetcher:dev have the first and not the
// second, because they were never pushed anywhere. That is precisely why the
// bundle manifest matters: for the two images this deployment most depends on,
// it is the only external statement of what they were.
func Inspect(ref string) (*ImageInfo, error) {
	id, err := Run("image", "inspect", ref, "--format", "{{.Id}}")
	if err != nil {
		return nil, fmt.Errorf(
			"image %s is not present.\n"+
				"  A bundle is built from images that exist. Run `make up` to build the\n"+
				"  nightglass images and pull the third-party ones, then bundle.", ref)
	}
	info := &ImageInfo{Ref: ref, ID: strings.TrimSpace(id)}

	digests, err := Run("image", "inspect", ref, "--format", "{{join .RepoDigests \"\\n\"}}")
	if err == nil {
		name := ref
		if i := strings.LastIndex(ref, ":"); i > strings.LastIndex(ref, "/") {
			name = ref[:i]
		}
		for _, line := range strings.Split(digests, "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, name+"@") {
				info.RepoDigest = strings.TrimPrefix(line, name+"@")
				break
			}
		}
	}
	return info, nil
}

// Save writes `docker save <ref>` to path.
//
// The output is an OCI layout: every file inside is named by its own sha256,
// with index.json as the entry point and manifest.json retained for `docker
// load`. So the bundle's integrity story nests — the member is hashed as a
// whole, and its contents are content-addressed underneath.
func Save(ref, path string) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()

	cmd := exec.Command("docker", "save", ref)
	var errb bytes.Buffer
	cmd.Stdout = f
	cmd.Stderr = &errb
	if err := cmd.Run(); err != nil {
		return &Err{Args: []string{"save", ref}, Stderr: errb.String(), Err: err}
	}
	return f.Sync()
}

// Load feeds an image archive to `docker load`.
func Load(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()

	cmd := exec.Command("docker", "load")
	cmd.Stdin = f
	var out, errb bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &errb
	if err := cmd.Run(); err != nil {
		return "", &Err{Args: []string{"load"}, Stderr: errb.String(), Err: err}
	}
	return strings.TrimSpace(out.String()), nil
}

// TarIntoVolume pipes a tar stream into a named volume through a helper image.
//
// This is the mechanism scripts/seed-models-from-host.sh already uses, for the
// same reason: a named volume lives under the daemon's storage root, owned by
// root, and the only portable way to write one is from inside a container. The
// helper image is carried in the bundle — alpine:3, four megabytes — because a
// bundle that needs a tool it does not ship is not a bundle.
func TarIntoVolume(volume, helperImage string, r io.Reader) error {
	if _, err := Run("volume", "create", volume); err != nil {
		return err
	}
	cmd := exec.Command("docker", "run", "--rm", "-i",
		"-v", volume+":/dest", helperImage, "tar", "-C", "/dest", "-xf", "-")
	cmd.Stdin = r
	var errb bytes.Buffer
	cmd.Stderr = &errb
	if err := cmd.Run(); err != nil {
		return &Err{Args: []string{"run", "--rm", "-i", "-v", volume + ":/dest", helperImage},
			Stderr: errb.String(), Err: err}
	}
	return nil
}

// TarFromVolume streams a subdirectory of a named volume out as a tar.
//
// Used at create time to read the ollama model store, which is root-owned for
// the same reason. The caller must consume r fully and then call wait.
func TarFromVolume(volume, helperImage, subdir string) (r io.ReadCloser, wait func() error, err error) {
	cmd := exec.Command("docker", "run", "--rm", "-i",
		"-v", volume+":/src:ro", helperImage, "tar", "-C", "/src", "-cf", "-", subdir)
	var errb bytes.Buffer
	cmd.Stderr = &errb
	out, err := cmd.StdoutPipe()
	if err != nil {
		return nil, nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, nil, err
	}
	wait = func() error {
		if err := cmd.Wait(); err != nil {
			return &Err{Args: []string{"run", "--rm", "-v", volume + ":/src:ro", helperImage},
				Stderr: errb.String(), Err: err}
		}
		return nil
	}
	return out, wait, nil
}

// VolumeExists reports whether a named volume is present.
func VolumeExists(name string) bool {
	_, err := Run("volume", "inspect", name)
	return err == nil
}
