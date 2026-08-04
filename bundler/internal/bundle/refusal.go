package bundle

import (
	"fmt"
	"strings"
)

// Refusal is a bundle that will not be accepted, and why.
//
// It is a distinct type from a plain error because the difference matters to a
// caller: an *os.PathError from a missing file means the check could not be
// run, and a Refusal means it ran and the answer was no. The exit codes follow
// that split — 1 for a refusal, 2 for anything that stopped the check from
// happening. A script that treats "could not open the bundle" as "the bundle is
// bad" is a script that will one day delete a good bundle because a disk was
// busy.
type Refusal struct {
	Code   string // stable, greppable: mismatch, truncated, missing, ...
	Path   string // the member it is about, if it is about one
	Detail []string
}

func (r *Refusal) Error() string {
	var b strings.Builder
	if r.Path != "" {
		fmt.Fprintf(&b, "%s: %s", r.Path, r.Code)
	} else {
		b.WriteString(r.Code)
	}
	for _, line := range r.Detail {
		b.WriteString("\n  ")
		b.WriteString(line)
	}
	return b.String()
}

func refuse(code, path string, detail ...string) *Refusal {
	return &Refusal{Code: code, Path: path, Detail: detail}
}

// The refusal codes. Every one of these exists because a verifier without it
// passes a bundle it should not — see the table in the design doc.
const (
	// CodeMismatch: the bytes are not the bytes the manifest describes.
	CodeMismatch = "sha256 mismatch"

	// CodeTruncated: the archive ended in the middle of a member. The
	// interesting case, because a truncated transfer is the single most likely
	// way a bundle goes wrong and it produces a file that opens fine.
	CodeTruncated = "truncated"

	// CodeMissing: a manifest entry that never appeared in the stream. This is
	// finding 55's shape — the archive reads cleanly to the end and is simply
	// missing something, so every check that looks only at what IS there
	// passes. It is the check a naive implementation forgets.
	CodeMissing = "listed in the manifest, never appeared in the archive"

	// CodeUnexpected: a member present that the manifest does not list.
	// The other half of set equality, and neither half is optional.
	CodeUnexpected = "present in the archive, not listed in the manifest"

	// CodeManifestNotFirst: the manifest is not the first member, so the
	// archive cannot be verified in one pass. Refused rather than quietly
	// falling back to staging 18 GB somewhere.
	CodeManifestNotFirst = "MANIFEST.json is not the first member"

	// CodeDuplicate: the same path twice in the archive.
	CodeDuplicate = "appears twice in the archive"

	// CodeMemberType: something that is not a regular file. A bundle is a flat
	// set of files by construction; a symlink or a device node in one is either
	// a bug in the writer or an attempt at something.
	CodeMemberType = "not a regular file"

	// CodeSize: the tar header disagrees with the manifest before a byte is
	// read. Cheap, and a clearer diagnosis than the hash mismatch it would
	// otherwise become.
	CodeSize = "size disagrees with the manifest"
)
