package bundle

import (
	"archive/tar"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/lunasilvestre/nightglass/bundler/internal/manifest"
)

// -- fixtures ----------------------------------------------------------------

type member struct {
	path string
	data []byte
	kind manifest.Kind
}

func members() []member {
	return []member{
		{"images/alpine_3.tar", bytes.Repeat([]byte("i"), 4096), manifest.KindImage},
		{"models/blobs/sha256-" + sum(bytes.Repeat([]byte("m"), 8192)),
			bytes.Repeat([]byte("m"), 8192), manifest.KindModelBlob},
		{"wheels/numpy-2.0-cp313.whl", bytes.Repeat([]byte("w"), 512), manifest.KindWheel},
		{"data/raw/ais/aisdk.zip", bytes.Repeat([]byte("d"), 1024), manifest.KindData},
	}
}

func sum(b []byte) string {
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

func manifestFor(t *testing.T, ms []member) []byte {
	t.Helper()
	entries := make([]manifest.Entry, 0, len(ms))
	for _, m := range ms {
		e := manifest.Entry{
			Path:   m.path,
			Bytes:  int64(len(m.data)),
			SHA256: sum(m.data),
			Kind:   m.kind,
		}
		if m.kind == manifest.KindImage {
			e.Image = &manifest.Image{Ref: "alpine:3", ID: "sha256:deadbeef"}
		}
		entries = append(entries, e)
	}
	raw, err := manifest.New("test", "2026-08-04T00:00:00Z", manifest.Source{}, entries).Marshal()
	if err != nil {
		t.Fatalf("marshalling the manifest: %v", err)
	}
	return raw
}

// writeTar lays out an archive by hand so a test can put the manifest
// somewhere other than first, or leave a member out of it.
func writeTar(t *testing.T, manifestRaw []byte, ms []member, manifestLast bool) []byte {
	t.Helper()
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	put := func(name string, data []byte) {
		if err := tw.WriteHeader(&tar.Header{
			Name: name, Mode: 0o644, Size: int64(len(data)), Typeflag: tar.TypeReg,
		}); err != nil {
			t.Fatalf("header %s: %v", name, err)
		}
		if _, err := tw.Write(data); err != nil {
			t.Fatalf("body %s: %v", name, err)
		}
	}
	if !manifestLast {
		put(manifest.Name, manifestRaw)
	}
	for _, m := range ms {
		put(m.path, m.data)
	}
	if manifestLast {
		put(manifest.Name, manifestRaw)
	}
	if err := tw.Close(); err != nil {
		t.Fatalf("closing: %v", err)
	}
	return buf.Bytes()
}

func good(t *testing.T) []byte {
	t.Helper()
	ms := members()
	return writeTar(t, manifestFor(t, ms), ms, false)
}

func verify(t *testing.T, raw []byte) (*Result, error) {
	t.Helper()
	return Verify(bytes.NewReader(raw), Options{})
}

// refusedWith asserts the error is a Refusal carrying code, and returns it so
// the caller can check the message. A plain error here means the check could
// not be run, which is a different outcome and must not be mistaken for a
// refusal — that distinction is what the two exit codes are for.
func refusedWith(t *testing.T, err error, code string) *Refusal {
	t.Helper()
	if err == nil {
		t.Fatalf("expected a refusal (%s), got none", code)
	}
	var r *Refusal
	if !errors.As(err, &r) {
		t.Fatalf("expected a *Refusal (%s), got %T: %v", code, err, err)
	}
	if r.Code != code {
		t.Fatalf("refused with %q, expected %q\n%v", r.Code, code, err)
	}
	return r
}

// -- the happy path ----------------------------------------------------------

func TestAWellFormedBundleVerifies(t *testing.T) {
	res, err := verify(t, good(t))
	if err != nil {
		t.Fatalf("a good bundle was refused: %v", err)
	}
	if res.Entries != 4 {
		t.Errorf("verified %d entries, expected 4", res.Entries)
	}
	if want := int64(4096 + 8192 + 512 + 1024); res.Bytes != want {
		t.Errorf("verified %d bytes, expected %d", res.Bytes, want)
	}
	if len(res.ManifestSHA256) != 64 {
		t.Errorf("manifest digest %q is not a sha256", res.ManifestSHA256)
	}
	if res.ByKind[manifest.KindWheel].Entries != 1 {
		t.Errorf("per-kind roll-up lost the wheel: %+v", res.ByKind)
	}
}

func TestTheManifestDigestIsStableAcrossReads(t *testing.T) {
	// The digest is the one value an operator carries out of band, so it has to
	// be a property of the bundle and not of the reading.
	raw := good(t)
	a, err := verify(t, raw)
	if err != nil {
		t.Fatal(err)
	}
	b, err := verify(t, raw)
	if err != nil {
		t.Fatal(err)
	}
	if a.ManifestSHA256 != b.ManifestSHA256 {
		t.Errorf("two reads of one bundle gave %s and %s", a.ManifestSHA256, b.ManifestSHA256)
	}
}

// -- the six refusals --------------------------------------------------------

func TestOneFlippedByteIsRefused(t *testing.T) {
	raw := good(t)
	// Land inside the largest member rather than in a header.
	i := bytes.Index(raw, bytes.Repeat([]byte("m"), 8192))
	if i < 0 {
		t.Fatal("could not find the member body to corrupt")
	}
	raw[i+4000] ^= 0xff

	r := refusedWith(t, mustErr(verify(t, raw)), CodeMismatch)
	if !strings.Contains(r.Error(), "models/blobs/") {
		t.Errorf("the refusal does not name the member:\n%v", r)
	}
	// Both digests must be printed. "They differ" without saying how is a
	// diagnosis nobody can act on.
	if strings.Count(r.Error(), "sha256") < 1 || !strings.Contains(r.Error(), "manifest ") {
		t.Errorf("the refusal does not print both digests:\n%v", r)
	}
}

func TestATruncatedArchiveIsRefusedAsTruncatedNotAsAMismatch(t *testing.T) {
	// A partial transfer is the most likely way a bundle goes wrong, and it
	// produces a file that opens without complaint. Reporting it as a hash
	// mismatch would send someone looking for corruption instead of for a
	// copy that stopped early.
	raw := good(t)
	r := refusedWith(t, mustErr(verify(t, raw[:len(raw)-6000])), CodeTruncated)
	if !strings.Contains(strings.ToLower(r.Error()), "ended") {
		t.Errorf("the refusal does not say the archive ended early:\n%v", r)
	}
}

func TestAMemberMissingFromTheArchiveIsRefused(t *testing.T) {
	// Finding 55's shape: the archive reads cleanly to EOF and every check that
	// looks at what IS present passes. This is the one a naive implementation
	// forgets, and it is the reason set equality is checked in both directions.
	ms := members()
	full := manifestFor(t, ms)
	raw := writeTar(t, full, ms[:len(ms)-1], false) // manifest lists 4, archive holds 3

	r := refusedWith(t, mustErr(verify(t, raw)), CodeMissing)
	if !strings.Contains(r.Error(), "data/raw/ais/aisdk.zip") {
		t.Errorf("the refusal does not name what is absent:\n%v", r)
	}
	if !strings.Contains(r.Error(), "1 of 4") {
		t.Errorf("the refusal does not count what is absent:\n%v", r)
	}
}

func TestAMemberNotInTheManifestIsRefused(t *testing.T) {
	ms := members()
	short := manifestFor(t, ms[:len(ms)-1]) // manifest lists 3, archive holds 4
	raw := writeTar(t, short, ms, false)

	r := refusedWith(t, mustErr(verify(t, raw)), CodeUnexpected)
	if !strings.Contains(r.Error(), "data/raw/ais/aisdk.zip") {
		t.Errorf("the refusal does not name the extra member:\n%v", r)
	}
}

func TestAManifestThatIsNotFirstIsRefused(t *testing.T) {
	// Not a corruption — a bundle written by something else, or reordered by a
	// well-meaning tar. It is refused rather than handled, because handling it
	// means staging the whole archive to make a second pass possible, and the
	// single-pass property is what makes an 18 GB verify practical.
	ms := members()
	raw := writeTar(t, manifestFor(t, ms), ms, true)
	refusedWith(t, mustErr(verify(t, raw)), CodeManifestNotFirst)
}

func TestADuplicatedMemberIsRefused(t *testing.T) {
	ms := members()
	dup := append(members(), ms[0]) // the same path twice in the archive
	raw := writeTar(t, manifestFor(t, ms), dup, false)
	refusedWith(t, mustErr(verify(t, raw)), CodeDuplicate)
}

// -- shapes that are not corruption but are still not verifiable -------------

func TestAHeaderThatLiesAboutSizeIsRefusedBeforeHashing(t *testing.T) {
	ms := members()
	raw := manifestFor(t, ms)
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	_ = tw.WriteHeader(&tar.Header{Name: manifest.Name, Size: int64(len(raw)), Typeflag: tar.TypeReg, Mode: 0o644})
	_, _ = tw.Write(raw)
	// Declare a smaller size than the manifest records.
	_ = tw.WriteHeader(&tar.Header{Name: ms[0].path, Size: 10, Typeflag: tar.TypeReg, Mode: 0o644})
	_, _ = tw.Write(ms[0].data[:10])
	_ = tw.Close()

	refusedWith(t, mustErr(Verify(bytes.NewReader(buf.Bytes()), Options{})), CodeSize)
}

func TestASymlinkMemberIsRefused(t *testing.T) {
	ms := members()
	raw := manifestFor(t, ms)
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	_ = tw.WriteHeader(&tar.Header{Name: manifest.Name, Size: int64(len(raw)), Typeflag: tar.TypeReg, Mode: 0o644})
	_, _ = tw.Write(raw)
	_ = tw.WriteHeader(&tar.Header{
		Name: ms[0].path, Linkname: "/etc/passwd", Typeflag: tar.TypeSymlink, Mode: 0o777,
	})
	_ = tw.Close()

	refusedWith(t, mustErr(Verify(bytes.NewReader(buf.Bytes()), Options{})), CodeMemberType)
}

func TestAnEmptyArchiveIsRefused(t *testing.T) {
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	_ = tw.Close()
	refusedWith(t, mustErr(Verify(bytes.NewReader(buf.Bytes()), Options{})), CodeManifestNotFirst)
}

// -- helpers -----------------------------------------------------------------

func mustErr(_ *Result, err error) error { return err }

func TestShortNeverSplitsTheTailOfAPath(t *testing.T) {
	// A sha256-named blob cut in half stops being greppable, which is the
	// progress-line version of finding 55. Keeping the END of the path is what
	// makes the elision safe.
	p := "models/blobs/sha256-2049f5674b1e92b4464e5729975c9689fcfbf0b0e4443ccf10b5339f370f9a54"
	got := short(p)
	// Runes, not bytes: fmt's %-46s pads in runes, and the ellipsis is one
	// column and three bytes. Measuring this in bytes is what the first
	// version did, and it put every elided line two columns out.
	if n := utf8.RuneCountInString(got); n > 46 {
		t.Errorf("short(%q) is %d runes, want <= 46", got, n)
	}
	if !strings.HasSuffix(p, strings.TrimPrefix(got, "…")) {
		t.Errorf("short() kept the head instead of the tail: %q", got)
	}
}

func TestCommasGroupsDigits(t *testing.T) {
	for _, tc := range []struct {
		in   int64
		want string
	}{
		{0, "0"}, {999, "999"}, {1000, "1,000"},
		{18041827328, "18,041,827,328"}, {1007713998, "1,007,713,998"},
	} {
		if got := commas(tc.in); got != tc.want {
			t.Errorf("commas(%d) = %q, want %q", tc.in, got, tc.want)
		}
	}
}
