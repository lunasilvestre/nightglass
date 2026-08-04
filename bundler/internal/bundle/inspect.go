package bundle

import (
	"archive/tar"
	"bytes"
	"fmt"
	"io"

	"github.com/lunasilvestre/nightglass/bundler/internal/manifest"
)

// ReadManifest reads the first member and stops.
//
// This is the payoff of manifest-first that is easy to miss: knowing what an
// 18 GB bundle contains costs one tar header and a few kilobytes, on a bundle
// sitting on read-only media, without a temporary file anywhere. Nothing it
// returns has been verified — the manifest describes the archive, and only
// Verify checks whether the archive agrees.
func ReadManifest(r io.Reader) ([]byte, *manifest.Manifest, error) {
	tr := tar.NewReader(r)
	hdr, err := tr.Next()
	if err == io.EOF {
		return nil, nil, refuse(CodeManifestNotFirst, "", "the archive is empty")
	}
	if err != nil {
		return nil, nil, fmt.Errorf("reading the first member: %w", err)
	}
	if hdr.Name != manifest.Name {
		return nil, nil, refuse(CodeManifestNotFirst, "",
			fmt.Sprintf("the first member is %q", hdr.Name))
	}
	raw, err := io.ReadAll(tr)
	if err != nil {
		return nil, nil, fmt.Errorf("reading %s: %w", manifest.Name, err)
	}
	m, err := manifest.Load(bytes.NewReader(raw))
	if err != nil {
		return nil, nil, err
	}
	return raw, m, nil
}
