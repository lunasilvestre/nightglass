package sources

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// fixture reproduces data/sources.yaml's real shape, including the folded
// `>-` notes and the role vocabulary, so the parser is exercised against what
// it will actually meet rather than against a simplified version of it.
const fixture = `
sar:
  licence: >-
    Contains modified Copernicus Sentinel data 2026, processed by ESA.
    Distributed by the Alaska Satellite Facility (ASF DAAC).
  credentials: earthdata
  out: /app/data/raw/sar
  items:
    - name: S1D_BC13.zip
      url: https://datapool.asf.alaska.edu/GRD_HD/SD/S1D_BC13.zip
      sha256: 06322098f3ba9e91b898c90da220e9acf3040339ac177cc2c289e3e9d02bbb8f
      bytes: 1007713998
      aoi: kattegat
      role: required
      note: >-
        The validation scene. Every M3 number in the README — 35 detections,
        21 matched, 14 unmatched, 104 m median match distance — is this granule.

    - name: S1A_2264.zip
      url: https://datapool.asf.alaska.edu/GRD_HD/SA/S1A_2264.zip
      sha256: 479dc240f3d5e75ee79d00a33a2f814a4fd692a6b341570de9917602ea64d88b
      bytes: 874202332
      aoi: leixoes
      role: optional
      note: The third configured AOI.

    - name: S1D_09C0.zip
      url: https://datapool.asf.alaska.edu/GRD_HD/SD/S1D_09C0.zip
      sha256: 9ff07cdf7cf92aa423ab4f013df9685cd8589559b09df8ff8ac86a2a275148d9
      bytes: 670769277
      aoi: lisbon
      role: superseded
      note: Path 23 descending. Selected first, and wrong.

ais:
  licence: Danish Maritime Authority — AIS data.
  credentials: none
  out: /app/data/raw/ais
  items:
    - name: aisdk-2026-07-17.zip
      url: http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/aisdk-2026-07-17.zip
      sha256: bb592db47ac03877c35acf9aa96ad380d83df7abc4f898c1d4721c56dc897116
      bytes: 889684392
      aoi: kattegat
      role: required
      note: The acquisition day.
`

func write(t *testing.T, body string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "sources.yaml")
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestTheCommittedManifestShapeParses(t *testing.T) {
	s, err := Load(write(t, fixture))
	if err != nil {
		t.Fatalf("data/sources.yaml's own shape was rejected: %v", err)
	}
	if len(s.SAR.Items) != 3 || len(s.AIS.Items) != 1 {
		t.Fatalf("parsed %d sar and %d ais items", len(s.SAR.Items), len(s.AIS.Items))
	}
	if s.SAR.Items[0].Bytes != 1007713998 {
		t.Errorf("byte count did not survive: %d", s.SAR.Items[0].Bytes)
	}
	if s.AIS.Items[0].Section != "ais" {
		t.Errorf("the ais item was not tagged with its section: %q", s.AIS.Items[0].Section)
	}
}

func TestAFoldedNoteBecomesOneLine(t *testing.T) {
	// The notes are `>-` blocks and arrive with embedded newlines. They end up
	// on a single terminal line at create time, so they are collapsed once here
	// rather than at every print site.
	s, err := Load(write(t, fixture))
	if err != nil {
		t.Fatal(err)
	}
	note := s.SAR.Items[0].Note
	if strings.Contains(note, "\n") {
		t.Errorf("the note kept its line breaks: %q", note)
	}
	if !strings.Contains(note, "104 m median match distance") {
		t.Errorf("collapsing the note lost part of it: %q", note)
	}
}

func TestSelectDefaultsToRequiredAndReportsWhatItSkipped(t *testing.T) {
	// Both halves are returned because both get printed. "Three of six granules
	// were bundled" and "three granules were bundled" are different claims, and
	// the fetchers already make the first one.
	s, err := Load(write(t, fixture))
	if err != nil {
		t.Fatal(err)
	}
	wanted, skipped := s.Select(false)
	if len(wanted) != 2 {
		t.Errorf("selected %d required items, expected 2", len(wanted))
	}
	if len(skipped) != 2 {
		t.Errorf("reported %d skipped items, expected 2 (optional + superseded)", len(skipped))
	}
	for _, it := range skipped {
		if it.Role == Required {
			t.Errorf("%s is required and was skipped", it.Name)
		}
	}

	all, none := s.Select(true)
	if len(all) != 4 || len(none) != 0 {
		t.Errorf("--all selected %d and skipped %d, expected 4 and 0", len(all), len(none))
	}
}

func TestSarComesBeforeAis(t *testing.T) {
	// The AIS slice window is derived from a granule's own acquisition time, so
	// the granules are the thing a restored site needs first. Ordering the
	// bundle the same way is free and means an interrupted read gets the more
	// useful half.
	s, err := Load(write(t, fixture))
	if err != nil {
		t.Fatal(err)
	}
	all := s.All()
	if all[len(all)-1].Section != "ais" {
		t.Errorf("ais is not last: %+v", all[len(all)-1])
	}
}

func TestAnUnknownRoleIsRefused(t *testing.T) {
	body := strings.Replace(fixture, "role: optional", "role: probably", 1)
	if _, err := Load(write(t, body)); err == nil {
		t.Error("an unknown role was accepted")
	}
}

func TestAnItemWithNoChecksumIsRefused(t *testing.T) {
	// The whole point of reading this file is the checksum. An entry without
	// one cannot be cross-checked, and bundling it would mean carrying bytes
	// under a digest of their own making.
	body := strings.Replace(fixture,
		"      sha256: bb592db47ac03877c35acf9aa96ad380d83df7abc4f898c1d4721c56dc897116\n", "", 1)
	if _, err := Load(write(t, body)); err == nil {
		t.Error("an item with no sha256 was accepted")
	}
}

func TestAMissingManifestSaysWhereItShouldBe(t *testing.T) {
	_, err := Load(filepath.Join(t.TempDir(), "nope.yaml"))
	if err == nil {
		t.Fatal("a missing manifest was accepted")
	}
	if !strings.Contains(err.Error(), "data/sources.yaml") {
		t.Errorf("the error does not say where the manifest lives: %v", err)
	}
}
