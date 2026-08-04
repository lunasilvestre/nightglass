package manifest

import (
	"strings"
	"testing"
)

func entry(path string) Entry {
	return Entry{
		Path:   path,
		Bytes:  10,
		SHA256: strings.Repeat("a", 64),
		Kind:   KindData,
	}
}

func load(t *testing.T, m *Manifest) error {
	t.Helper()
	raw, err := m.Marshal()
	if err != nil {
		t.Fatalf("marshalling: %v", err)
	}
	_, err = Load(strings.NewReader(string(raw)))
	return err
}

func TestAManifestRoundTrips(t *testing.T) {
	m := New("nightglass-bundle test", "2026-08-04T00:00:00Z",
		Source{GitCommit: "d268ee5", Docker: "29.7.1"},
		[]Entry{
			{Path: "images/alpine_3.tar", Bytes: 4096, SHA256: strings.Repeat("b", 64),
				Kind: KindImage, Image: &Image{Ref: "alpine:3", ID: "sha256:x"}},
			{Path: "data/raw/ais/aisdk.zip", Bytes: 889684392, SHA256: strings.Repeat("c", 64),
				Kind: KindData, Role: "required", Note: "The acquisition day."},
		})

	raw, err := m.Marshal()
	if err != nil {
		t.Fatal(err)
	}
	got, err := Load(strings.NewReader(string(raw)))
	if err != nil {
		t.Fatalf("a manifest this package wrote was rejected on read: %v", err)
	}
	if got.Totals.Bytes != 4096+889684392 {
		t.Errorf("totals did not survive: %d", got.Totals.Bytes)
	}
	if got.Entries[1].Note != "The acquisition day." {
		t.Errorf("the note did not survive: %q", got.Entries[1].Note)
	}
	if got.Entries[0].Image.Ref != "alpine:3" {
		t.Errorf("the image ref did not survive: %+v", got.Entries[0].Image)
	}
}

func TestSrcIsNotSerialised(t *testing.T) {
	// Src is the build machine's path to the file. It is an input to the
	// writer, not a fact about the bundle, and a manifest that recorded where
	// on someone's disk a granule happened to live would be carrying something
	// it has no reason to carry across an air gap.
	m := New("t", "2026-08-04T00:00:00Z", Source{}, []Entry{
		{Path: "data/x.zip", Bytes: 1, SHA256: strings.Repeat("d", 64),
			Kind: KindData, Src: "/home/someone/Documents/dev/nightglass/data/raw/x.zip"},
	})
	raw, err := m.Marshal()
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "/home/someone") {
		t.Errorf("the manifest leaked a host path:\n%s", raw)
	}
}

func TestAnUnknownFormatIsRefusedRatherThanPartlyRead(t *testing.T) {
	// A bundle outlives the tool that wrote it. Reading the fields that happen
	// to look familiar out of a future format is how a verifier ends up
	// confidently checking half a manifest.
	raw := `{"format":"nightglass-bundle/2","created":"","tool":"","source":{"git_dirty":false},
	         "totals":{"entries":1,"bytes":10},
	         "entries":[{"path":"a","bytes":10,"sha256":"` + strings.Repeat("a", 64) + `","kind":"data"}]}`
	err := mustFail(t, raw)
	if !strings.Contains(err.Error(), "nightglass-bundle/2") {
		t.Errorf("the refusal does not name the format it found: %v", err)
	}
}

func TestUnknownFieldsAreRefused(t *testing.T) {
	raw := `{"format":"` + Format + `","created":"","tool":"","source":{"git_dirty":false},
	         "totals":{"entries":1,"bytes":10},"surprise":true,
	         "entries":[{"path":"a","bytes":10,"sha256":"` + strings.Repeat("a", 64) + `","kind":"data"}]}`
	mustFail(t, raw)
}

func TestPathsThatCouldEscapeTheDestinationAreRefused(t *testing.T) {
	// Checked at load, not at extract. A bundle containing ../../etc/passwd is
	// not a bundle with one bad member to skip; it is a bundle to refuse, and
	// saying so before a byte has been written is the useful moment.
	for _, p := range []string{
		"../etc/passwd",
		"data/../../etc/passwd",
		"/etc/passwd",
		"data//x.zip",
		"./data/x.zip",
		`data\x.zip`,
		"",
	} {
		m := New("t", "", Source{}, []Entry{entry(p)})
		if err := load(t, m); err == nil {
			t.Errorf("path %q was accepted", p)
		}
	}
}

func TestAManifestMustNotListItself(t *testing.T) {
	m := New("t", "", Source{}, []Entry{entry(Name)})
	if err := load(t, m); err == nil {
		t.Errorf("%s listed as one of its own entries was accepted", Name)
	}
}

func TestADuplicatePathIsRefused(t *testing.T) {
	// Two members with one path is ambiguous: at restore the later one wins,
	// silently, and which one that is depends on write order.
	m := New("t", "", Source{}, []Entry{entry("data/x.zip"), entry("data/x.zip")})
	err := load(t, m)
	if err == nil {
		t.Fatal("a duplicate path was accepted")
	}
	if !strings.Contains(err.Error(), "twice") {
		t.Errorf("unhelpful refusal: %v", err)
	}
}

func TestTotalsMustAgreeWithTheEntries(t *testing.T) {
	// Redundant with the entry list, and that is the point: it is a cheap check
	// that the list was not edited without the summary being updated to match.
	m := New("t", "", Source{}, []Entry{entry("data/x.zip")})
	m.Totals.Bytes = 999
	if err := load(t, m); err == nil {
		t.Error("a manifest whose totals disagree with its entries was accepted")
	}

	m2 := New("t", "", Source{}, []Entry{entry("data/x.zip")})
	m2.Totals.Entries = 7
	if err := load(t, m2); err == nil {
		t.Error("a manifest whose entry count is wrong was accepted")
	}
}

func TestADigestThatIsNotASha256IsRefused(t *testing.T) {
	for _, bad := range []string{
		"", "abc",
		strings.Repeat("A", 64), // uppercase — hex comparison is done on lowercase
		strings.Repeat("a", 63),
		strings.Repeat("z", 64),
		"sha256:" + strings.Repeat("a", 64),
	} {
		e := entry("data/x.zip")
		e.SHA256 = bad
		if err := load(t, New("t", "", Source{}, []Entry{e})); err == nil {
			t.Errorf("sha256 %q was accepted", bad)
		}
	}
}

func TestAnImageEntryWithNoRefIsRefused(t *testing.T) {
	// Nothing could load it, so it is not an image entry — it is a file with a
	// misleading kind.
	e := entry("images/x.tar")
	e.Kind = KindImage
	if err := load(t, New("t", "", Source{}, []Entry{e})); err == nil {
		t.Error("an image entry with no ref was accepted")
	}
}

func TestAnUnknownKindIsRefused(t *testing.T) {
	e := entry("x")
	e.Kind = Kind("firmware")
	err := load(t, New("t", "", Source{}, []Entry{e}))
	if err == nil {
		t.Fatal("an unknown kind was accepted")
	}
	if !strings.Contains(err.Error(), "model-blob") {
		t.Errorf("the refusal does not list what the known kinds are: %v", err)
	}
}

func TestByPathIndexesEveryEntry(t *testing.T) {
	m := New("t", "", Source{}, []Entry{entry("a"), entry("b"), entry("c")})
	idx := m.ByPath()
	if len(idx) != 3 {
		t.Fatalf("indexed %d of 3", len(idx))
	}
	if idx["b"].Path != "b" {
		t.Errorf("index points at the wrong entry: %+v", idx["b"])
	}
}

func mustFail(t *testing.T, raw string) error {
	t.Helper()
	_, err := Load(strings.NewReader(raw))
	if err == nil {
		t.Fatalf("accepted a manifest it should have refused:\n%s", raw)
	}
	return err
}
