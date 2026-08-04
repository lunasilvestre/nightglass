package bundle

import (
	"archive/tar"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"

	"github.com/lunasilvestre/nightglass/bundler/internal/manifest"
)

// copyBuf is the only buffer this package holds. Verifying an 18 GB bundle
// costs one megabyte of memory and one sequential read, which is the property
// that decides whether the tool gets run or avoided.
const copyBuf = 1 << 20

// Options controls the noise, not the checks. There is no option that skips a
// check: a verify with a check turned off is a different claim wearing the same
// word, and the exit code would not say which one had been made.
type Options struct {
	Verbose  bool
	Progress io.Writer
	TTY      bool
}

// KindStat is the per-kind roll-up printed at the end.
type KindStat struct {
	Entries int
	Bytes   int64
}

// Result is what a bundle turned out to contain.
type Result struct {
	Manifest       *manifest.Manifest
	ManifestSHA256 string
	Entries        int
	Bytes          int64
	ByKind         map[manifest.Kind]*KindStat
}

// sinkFunc supplies a destination for a member's bytes, or nil to discard them.
//
// This is the seam that makes restore free: verify and restore are the same
// single pass over the same six checks, differing only in whether the bytes are
// written down. A restore that re-implemented the checks would be a restore
// whose checks could drift from verify's.
type sinkFunc func(e *manifest.Entry) (io.WriteCloser, error)

// Verify streams an archive and checks it against its own manifest.
//
// The manifest must be the first member. Everything after it is hashed as it
// passes, and nothing is retained. Returns a *Refusal if the bundle is bad and
// an ordinary error if the check could not be completed.
func Verify(r io.Reader, opt Options) (*Result, error) {
	return walk(r, opt, nil)
}

func walk(r io.Reader, opt Options, sink sinkFunc) (*Result, error) {
	tr := tar.NewReader(r)

	// -- the manifest, which must be first --------------------------------
	hdr, err := tr.Next()
	if err == io.EOF {
		return nil, refuse(CodeManifestNotFirst, "",
			"the archive is empty")
	}
	if err != nil {
		return nil, fmt.Errorf("reading the first member: %w", err)
	}
	if hdr.Name != manifest.Name {
		return nil, refuse(CodeManifestNotFirst, "",
			fmt.Sprintf("the first member is %q", hdr.Name),
			"A bundle is verified in one sequential pass, which needs the manifest",
			"before the members it describes. Refusing rather than falling back to",
			"staging the whole archive somewhere to make a second pass possible.")
	}

	mh := sha256.New()
	m, err := manifest.Load(io.TeeReader(tr, mh))
	if err != nil {
		return nil, err
	}
	manifestSum := hex.EncodeToString(mh.Sum(nil))

	res := &Result{
		Manifest:       m,
		ManifestSHA256: manifestSum,
		ByKind:         map[manifest.Kind]*KindStat{},
	}
	want := m.ByPath()
	seen := make(map[string]bool, len(want))

	if opt.Progress != nil {
		fmt.Fprintf(opt.Progress, "   %-46s %d entries, %s\n",
			manifest.Name, m.Totals.Entries, commas(m.Totals.Bytes))
	}

	// -- everything else ---------------------------------------------------
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if errors.Is(err, io.ErrUnexpectedEOF) {
			return nil, refuse(CodeTruncated, "",
				"the archive ends in the middle of a member header",
				"A partial transfer produces exactly this. Re-copy the bundle; the",
				"manifest digest tells you whether the source is intact.")
		}
		if err != nil {
			return nil, fmt.Errorf("reading the archive: %w", err)
		}

		name := hdr.Name
		if hdr.Typeflag != tar.TypeReg {
			return nil, refuse(CodeMemberType, name,
				fmt.Sprintf("tar type flag %q", string(hdr.Typeflag)),
				"A bundle is a flat set of regular files by construction.")
		}
		if seen[name] {
			return nil, refuse(CodeDuplicate, name)
		}
		e, ok := want[name]
		if !ok {
			return nil, refuse(CodeUnexpected, name,
				"Something was added to this archive after the manifest was written.")
		}
		if hdr.Size != e.Bytes {
			return nil, refuse(CodeSize, name,
				fmt.Sprintf("header says %s, manifest says %s",
					commas(hdr.Size), commas(e.Bytes)))
		}
		seen[name] = true

		if err := consume(tr, e, opt, sink); err != nil {
			return nil, err
		}

		res.Entries++
		res.Bytes += e.Bytes
		st := res.ByKind[e.Kind]
		if st == nil {
			st = &KindStat{}
			res.ByKind[e.Kind] = st
		}
		st.Entries++
		st.Bytes += e.Bytes

		if opt.Verbose && opt.Progress != nil {
			fmt.Fprintf(opt.Progress, "   %-46s %14s  ok\n", short(name), commas(e.Bytes))
		} else if opt.Progress != nil && opt.TTY {
			fmt.Fprintf(opt.Progress, "\r   %-46s %14s", short(name), commas(res.Bytes))
		}
	}
	if opt.Progress != nil && opt.TTY && !opt.Verbose {
		fmt.Fprintf(opt.Progress, "\r%s\r", strings.Repeat(" ", 64))
	}

	// -- the check a naive implementation forgets --------------------------
	//
	// Everything above only looks at members that are present. An archive that
	// is simply missing one reads cleanly to EOF and passes every one of them.
	var absent []string
	for p := range want {
		if !seen[p] {
			absent = append(absent, p)
		}
	}
	if len(absent) > 0 {
		sort.Strings(absent)
		detail := []string{
			fmt.Sprintf("%d of %d entries are absent:", len(absent), len(want)),
		}
		for _, p := range absent {
			detail = append(detail, "    "+p)
		}
		detail = append(detail,
			"",
			"The archive read cleanly to the end. Every check that looks at what is",
			"present passed, because what is wrong here is what is not present.")
		return nil, refuse(CodeMissing, "", detail...)
	}

	return res, nil
}

// consume hashes one member, optionally writing it somewhere.
func consume(tr io.Reader, e *manifest.Entry, opt Options, sink sinkFunc) error {
	h := sha256.New()
	var dst io.Writer = h
	var closer io.WriteCloser

	if sink != nil {
		w, err := sink(e)
		if err != nil {
			return err
		}
		if w != nil {
			closer = w
			dst = io.MultiWriter(h, w)
		}
	}

	n, err := io.CopyBuffer(dst, tr, make([]byte, copyBuf))
	if closer != nil {
		if cerr := closer.Close(); cerr != nil && err == nil {
			err = cerr
		}
	}
	if errors.Is(err, io.ErrUnexpectedEOF) {
		return refuse(CodeTruncated, e.Path,
			fmt.Sprintf("read %s of %s before the archive ended",
				commas(n), commas(e.Bytes)),
			"A partial transfer produces exactly this, and the file it leaves behind",
			"opens without complaint.")
	}
	if err != nil {
		return fmt.Errorf("%s: %w", e.Path, err)
	}
	if n != e.Bytes {
		return refuse(CodeSize, e.Path,
			fmt.Sprintf("read %s, manifest says %s", commas(n), commas(e.Bytes)))
	}

	got := hex.EncodeToString(h.Sum(nil))
	if got != e.SHA256 {
		return refuse(CodeMismatch, e.Path,
			"sha256   "+got,
			"manifest "+e.SHA256,
			"The bytes differ from the ones this bundle was built from. Nothing is",
			"restored from a bundle that fails here.")
	}
	return nil
}

// short trims a long path from the left, keeping the end, because the end is
// the part that identifies the file. Never mid-token: a sha256-named blob cut
// in half stops being greppable, which is finding 55 applied to a progress
// line.
// Counted in runes, not bytes, because that is what fmt's width counts — the
// ellipsis is one column and three bytes, and sizing this in bytes puts every
// elided line two columns out.
func short(p string) string {
	const width = 46
	r := []rune(p)
	if len(r) <= width {
		return p
	}
	return "…" + string(r[len(r)-(width-1):])
}

func commas(n int64) string {
	s := fmt.Sprintf("%d", n)
	if n < 0 {
		return s
	}
	var out []byte
	for i, c := range []byte(s) {
		if i > 0 && (len(s)-i)%3 == 0 {
			out = append(out, ',')
		}
		out = append(out, c)
	}
	return string(out)
}
