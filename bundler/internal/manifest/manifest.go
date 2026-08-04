// Package manifest is the bundle's statement of what it contains.
//
// It is deliberately the same shape as data/sources.yaml, which M6 introduced
// for the opposite direction: that manifest says where a byte came from and
// what it must hash to, so a clone can fetch it; this one says the same thing
// about a byte you are already holding. The fields carried over verbatim are
// sha256, bytes, role and note. The one dropped is url, because there is no
// url — the bytes are in the tar.
//
// Two properties are worth stating because the rest of the package depends on
// them:
//
// The manifest is the FIRST member of the archive. That is what lets verify be
// a single sequential pass — you know what to expect before you meet it — and a
// single sequential pass is what lets an 18 GB bundle be checked on a pipe as
// it comes off the transfer medium, rather than staged to disk first.
//
// The manifest cannot verify itself. Its own digest is printed by create and by
// verify, and written to a sidecar; that one 64-hex string is what an operator
// carries out of band. This is integrity, not authenticity — it says the bundle
// you have is the bundle that was built, and nothing whatsoever about who built
// it. Signing is a key-custody problem and this does not pretend to solve it.
package manifest

import (
	"encoding/json"
	"fmt"
	"io"
	"path"
	"regexp"
	"sort"
	"strings"
)

// Name is where the manifest lives inside the archive, and it must sort first
// because it is written first — not because tar sorts anything, but because
// create writes it before it writes anything else.
const Name = "MANIFEST.json"

// Format is the value of the "format" field. It is versioned because a bundle
// outlives the tool that wrote it; a reader that does not recognise the version
// must refuse rather than guess at the fields it does understand.
const Format = "nightglass-bundle/1"

// Kind classifies an entry by what has to happen to it at restore.
type Kind string

const (
	KindImage         Kind = "image"          // docker load
	KindModelBlob     Kind = "model-blob"     // into the ollama volume
	KindModelManifest Kind = "model-manifest" // likewise, but tiny and JSON
	KindWheel         Kind = "wheel"          // pip install --no-index
	KindData          Kind = "data"           // into the clone's data/raw tree
)

var kinds = map[Kind]bool{
	KindImage: true, KindModelBlob: true, KindModelManifest: true,
	KindWheel: true, KindData: true,
}

// Image is the provenance of a KindImage entry.
//
// Ref is the tag the image was saved under and the tag it will load back as.
// ID is the local content digest.
//
// RepoDigest is whatever `docker image inspect` reports under RepoDigests, and
// it is worth being precise about what that is NOT. Docker records the field
// for a locally built image too — `nightglass/app:dev` carries
// `nightglass/app@sha256:dc851b20…` — and that value resolves against no
// registry anywhere, because the image was never pushed. Nothing available at
// create time distinguishes the two cases: telling them apart means asking a
// registry, which is the one thing a bundler for air-gapped sites must not do.
//
// So the field means "the digest docker reported", not "a digest you can pull".
// For ollama, postgis, qdrant and alpine it happens to be both. For the two
// images this deployment most depends on it is only the first — which is the
// argument for this manifest existing at all, rather than a defect in it.
type Image struct {
	Ref        string `json:"ref"`
	ID         string `json:"id"`
	RepoDigest string `json:"repo_digest,omitempty"`
}

// Entry is one file in the bundle.
type Entry struct {
	Path   string `json:"path"`
	Bytes  int64  `json:"bytes"`
	SHA256 string `json:"sha256"`
	Kind   Kind   `json:"kind"`

	Image *Image `json:"image,omitempty"`

	// Role and Note are carried through from data/sources.yaml for KindData,
	// so a bundle explains its own contents to the same degree the committed
	// manifest does.
	Role string `json:"role,omitempty"`
	Note string `json:"note,omitempty"`

	// Src is the host path this entry was read from at create time. It is not
	// serialised: it is an input to the writer, not a fact about the bundle,
	// and a bundle that recorded the build machine's directory layout would be
	// leaking something it has no reason to carry.
	Src string `json:"-"`
}

// Source records what built the bundle, so a restored site can tell which
// commit of the repository its images correspond to.
type Source struct {
	GitCommit string `json:"git_commit,omitempty"`
	GitDirty  bool   `json:"git_dirty"`
	Docker    string `json:"docker,omitempty"`
}

// Totals are redundant with Entries and that is the point: they are a cheap
// cross-check that the entry list was not edited without the summary being
// updated to match.
type Totals struct {
	Entries int   `json:"entries"`
	Bytes   int64 `json:"bytes"`
}

// Manifest is the whole document.
type Manifest struct {
	Format  string  `json:"format"`
	Created string  `json:"created"`
	Tool    string  `json:"tool"`
	Source  Source  `json:"source"`
	Totals  Totals  `json:"totals"`
	Entries []Entry `json:"entries"`
}

var hex64 = regexp.MustCompile(`^[0-9a-f]{64}$`)

// New builds a manifest around a set of entries, filling in the totals.
func New(tool string, created string, src Source, entries []Entry) *Manifest {
	var total int64
	for _, e := range entries {
		total += e.Bytes
	}
	return &Manifest{
		Format:  Format,
		Created: created,
		Tool:    tool,
		Source:  src,
		Totals:  Totals{Entries: len(entries), Bytes: total},
		Entries: entries,
	}
}

// Marshal renders the manifest as indented JSON with a trailing newline.
//
// Indented rather than compact because this is the one member of the bundle a
// human is expected to read — `tar -xOf bundle.tar MANIFEST.json` should give
// something legible without a formatter, on a machine that may not have one.
func (m *Manifest) Marshal() ([]byte, error) {
	buf, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(buf, '\n'), nil
}

// Load parses and validates a manifest.
//
// Validation is strict and happens before any bytes are compared, because
// every check here is a way the manifest could describe an archive that cannot
// be verified at all — and refusing early with a reason beats refusing late
// with a mismatch that has a different cause.
func Load(r io.Reader) (*Manifest, error) {
	raw, err := io.ReadAll(r)
	if err != nil {
		return nil, fmt.Errorf("reading %s: %w", Name, err)
	}
	var m Manifest
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&m); err != nil {
		return nil, fmt.Errorf("%s is not valid: %w", Name, err)
	}
	if err := m.Validate(); err != nil {
		return nil, err
	}
	return &m, nil
}

// Validate checks the manifest is internally coherent.
func (m *Manifest) Validate() error {
	if m.Format != Format {
		return fmt.Errorf(
			"%s says format %q, this tool reads %q.\n"+
				"A bundle outlives the tool that wrote it. Refusing rather than guessing\n"+
				"at the fields that happen to look familiar.",
			Name, m.Format, Format)
	}
	if len(m.Entries) == 0 {
		return fmt.Errorf("%s lists no entries", Name)
	}

	seen := make(map[string]bool, len(m.Entries))
	var total int64
	for i, e := range m.Entries {
		where := fmt.Sprintf("%s entry %d (%s)", Name, i, e.Path)
		if err := validPath(e.Path); err != nil {
			return fmt.Errorf("%s: %w", where, err)
		}
		if seen[e.Path] {
			return fmt.Errorf(
				"%s: listed twice.\n"+
					"Two members with one path is ambiguous — at restore the later one would\n"+
					"win, silently, and which one that is depends on write order.", where)
		}
		seen[e.Path] = true

		if !hex64.MatchString(e.SHA256) {
			return fmt.Errorf("%s: sha256 %q is not 64 lowercase hex characters", where, e.SHA256)
		}
		if e.Bytes < 0 {
			return fmt.Errorf("%s: negative size %d", where, e.Bytes)
		}
		if !kinds[e.Kind] {
			return fmt.Errorf("%s: unknown kind %q (have %s)", where, e.Kind, kindList())
		}
		if e.Kind == KindImage && (e.Image == nil || e.Image.Ref == "") {
			return fmt.Errorf("%s: kind image with no image ref — nothing could load it", where)
		}
		total += e.Bytes
	}

	if m.Totals.Entries != len(m.Entries) {
		return fmt.Errorf(
			"%s: totals say %d entries, the list has %d",
			Name, m.Totals.Entries, len(m.Entries))
	}
	if m.Totals.Bytes != total {
		return fmt.Errorf(
			"%s: totals say %d bytes, the entries sum to %d",
			Name, m.Totals.Bytes, total)
	}
	return nil
}

// ByPath indexes the entries. Validate has already rejected duplicates, so the
// map is complete.
func (m *Manifest) ByPath() map[string]*Entry {
	out := make(map[string]*Entry, len(m.Entries))
	for i := range m.Entries {
		out[m.Entries[i].Path] = &m.Entries[i]
	}
	return out
}

// validPath rejects anything that could escape the destination directory at
// restore, plus the shapes that are merely ambiguous.
//
// This runs at load, not at extract. A bundle that contains "../../etc/passwd"
// is not a bundle with one bad member to be skipped; it is a bundle to refuse,
// and saying so before a single byte has been written is the useful moment.
func validPath(p string) error {
	switch {
	case p == "":
		return fmt.Errorf("empty path")
	case p == Name:
		return fmt.Errorf("must not list itself — the manifest is not one of its own entries")
	case strings.HasPrefix(p, "/"):
		return fmt.Errorf("absolute path")
	case strings.Contains(p, `\`):
		return fmt.Errorf("backslash in path")
	case p != path.Clean(p):
		return fmt.Errorf("not in canonical form (want %q)", path.Clean(p))
	}
	for _, seg := range strings.Split(p, "/") {
		if seg == ".." {
			return fmt.Errorf("escapes the bundle root")
		}
	}
	return nil
}

func kindList() string {
	out := make([]string, 0, len(kinds))
	for k := range kinds {
		out = append(out, string(k))
	}
	sort.Strings(out)
	return strings.Join(out, ", ")
}
