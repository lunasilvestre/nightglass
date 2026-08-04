// Package sources reads data/sources.yaml — the manifest M6 committed.
//
// The bundler reads it for one reason: to refuse to bundle a granule whose
// bytes on disk are not the bytes that manifest declares. Without that check a
// bundle is a fresh set of unsourced bytes wearing a checksum of its own
// making, and the chain back to "every number in the README was measured over
// this file" is broken at exactly the point it was built to survive.
//
// Only the fields the bundler needs are read. `url` is deliberately not one of
// them: there is no URL in a bundle, the bytes are in the tar, and carrying a
// download address into an air-gapped site would be carrying the one thing it
// cannot act on.
package sources

import (
	"fmt"
	"os"
	"strings"

	"gopkg.in/yaml.v3"
)

// Role is data/sources.yaml's own vocabulary, kept verbatim.
//
//	required     a proof or the demo will not run without it
//	optional     catalogued and part of the picture; no number depends on it
//	superseded   kept because the mistake is instructive
type Role string

const (
	Required   Role = "required"
	Optional   Role = "optional"
	Superseded Role = "superseded"
)

// Item is one external file as the committed manifest declares it.
type Item struct {
	Name    string `yaml:"name"`
	SHA256  string `yaml:"sha256"`
	Bytes   int64  `yaml:"bytes"`
	AOI     string `yaml:"aoi"`
	Role    Role   `yaml:"role"`
	Note    string `yaml:"note"`
	Section string `yaml:"-"` // "sar" or "ais"
}

// Section is one archive — sar or ais.
type Section struct {
	Licence string `yaml:"licence"`
	Out     string `yaml:"out"`
	Items   []Item `yaml:"items"`
}

// Sources is the whole document. Only the two sections the bundler carries are
// modelled; a third would be ignored rather than rejected, because this file
// belongs to the fetchers and they are entitled to grow it.
type Sources struct {
	SAR Section `yaml:"sar"`
	AIS Section `yaml:"ais"`
}

// Load reads and lightly validates the committed manifest.
func Load(path string) (*Sources, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf(
			"no manifest at %s.\n"+
				"It is committed at data/sources.yaml and it is what makes a bundle's\n"+
				"data half traceable. Run from the repository root, or pass --repo.",
			path)
	}
	var s Sources
	if err := yaml.Unmarshal(raw, &s); err != nil {
		return nil, fmt.Errorf("%s is not valid YAML: %w", path, err)
	}
	s.SAR.tag("sar")
	s.AIS.tag("ais")
	for _, it := range s.All() {
		if it.SHA256 == "" || it.Bytes == 0 {
			return nil, fmt.Errorf("%s: %s has no sha256 or no byte count", path, it.Name)
		}
		switch it.Role {
		case Required, Optional, Superseded:
		default:
			return nil, fmt.Errorf("%s: %s has role %q", path, it.Name, it.Role)
		}
	}
	return &s, nil
}

func (s *Section) tag(name string) {
	for i := range s.Items {
		s.Items[i].Section = name
		s.Items[i].SHA256 = strings.ToLower(strings.TrimSpace(s.Items[i].SHA256))
		s.Items[i].Note = strings.Join(strings.Fields(s.Items[i].Note), " ")
	}
}

// All returns every item in both sections, sar first.
func (s *Sources) All() []Item {
	out := make([]Item, 0, len(s.SAR.Items)+len(s.AIS.Items))
	out = append(out, s.SAR.Items...)
	out = append(out, s.AIS.Items...)
	return out
}

// Select returns the items whose role is wanted.
//
// It returns the skipped ones too, and every caller prints them. "Three of six
// granules were bundled" and "three granules were bundled" are different
// claims, and the fetchers already make the first one; a bundle that quietly
// made the second would be the more comfortable and less true of the two.
func (s *Sources) Select(all bool) (wanted, skipped []Item) {
	for _, it := range s.All() {
		if all || it.Role == Required {
			wanted = append(wanted, it)
		} else {
			skipped = append(skipped, it)
		}
	}
	return wanted, skipped
}
