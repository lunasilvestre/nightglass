// Command nightglass-bundle carries this deployment across an air gap.
//
//	nightglass-bundle create   images + model blobs + wheels + data -> one tar
//	nightglass-bundle verify   stream it, check every byte against the manifest
//	nightglass-bundle restore  unpack it, then load it into docker
//	nightglass-bundle inspect  read the manifest without reading the bundle
//
// A clone of this repository reproduces the demo by fetching 6.2 GB from four
// hosts. A site with no route to any of them cannot, and one of those hosts —
// the Danish Maritime Authority's — serves a rolling eighteen-month window, so
// the Danish validation stops being reproducible from the committed manifest
// the day aisdk-2026-07-17 ages out. This is the artifact that outlives that.
//
// It is Go for one reason that survives scrutiny: the thing that unpacks an
// air-gapped bundle cannot itself need a Python environment to exist first.
// CGO_ENABLED=0, no interpreter, no shared libraries, one file. `docker` is the
// only thing it expects to find, and any host that could restore a bundle has
// it already.
package main

import (
	"bufio"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/lunasilvestre/nightglass/bundler/internal/bundle"
	"github.com/lunasilvestre/nightglass/bundler/internal/manifest"
)

// Version travels into the manifest, so a bundle says which tool wrote it.
const Version = "0.3.1"

// defaultImages is the stack, in load order.
//
// alpine:3 is here and is not part of the stack. It is the tool that writes the
// ollama named volume — the same mechanism scripts/seed-models-from-host.sh
// uses, because a volume lives under the daemon's storage root and the only
// portable way to write one is from inside a container. Four megabytes, and the
// bundle stops depending on something it does not carry.
var defaultImages = []string{
	"alpine:3",
	"nightglass/app:dev",
	"nightglass/fetcher:dev",
	"ollama/ollama:0.32.5",
	"postgis/postgis:17-3.5",
	"qdrant/qdrant:v1.18.3",
}

const (
	defaultVolume = "nightglass_ollama_models"
	defaultHelper = "alpine:3"
	defaultPython = "nightglass/app:dev"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "create":
		err = cmdCreate(os.Args[2:])
	case "verify":
		err = cmdVerify(os.Args[2:])
	case "restore":
		err = cmdRestore(os.Args[2:])
	case "inspect":
		err = cmdInspect(os.Args[2:])
	case "-h", "--help", "help":
		usage()
		return
	case "--version":
		fmt.Println("nightglass-bundle", Version)
		return
	default:
		fmt.Fprintf(os.Stderr, "unknown subcommand %q\n\n", os.Args[1])
		usage()
		os.Exit(2)
	}
	if err != nil {
		exit(err)
	}
}

// exit splits the two outcomes that must never be confused.
//
//	1  the check ran and the answer is no
//	2  the check could not be run
//
// A script that treats "could not open the bundle" as "the bundle is bad" is a
// script that will one day delete a good bundle because a disk was busy.
func exit(err error) {
	var r *bundle.Refusal
	if errors.As(err, &r) {
		fmt.Fprintf(os.Stderr, "\n\033[1mREFUSED\033[0m  %v\n", err)
		os.Exit(1)
	}
	fmt.Fprintf(os.Stderr, "\nERROR  %v\n", err)
	os.Exit(2)
}

func usage() {
	fmt.Fprint(os.Stderr, `nightglass-bundle `+Version+` — the offline transfer bundle

  create   -o FILE [--repo DIR] [--staging DIR] [--all] [--skip-wheels]
  verify   FILE | -                     [-v]
  restore  FILE --into DIR [--install --repo DIR]
  inspect  FILE                         [--json]

`+"`verify -`"+` reads stdin, so a bundle can be checked as it comes off the
transfer medium rather than after it has been staged:

  cat /media/usb/nightglass-bundle-`+Version+`.tar | nightglass-bundle verify -

Exit codes: 0 verified, 1 refused, 2 could not be checked.
`)
}

// -- create ------------------------------------------------------------------

func cmdCreate(args []string) error {
	fs := flag.NewFlagSet("create", flag.ExitOnError)
	out := fs.String("o", "", "output tarball (required)")
	repo := fs.String("repo", ".", "the clone to read data/ and data/sources.yaml from")
	staging := fs.String("staging", "", "scratch space (default <out>.staging)")
	all := fs.Bool("all", false, "include data/sources.yaml's optional and superseded entries")
	skipWheels := fs.Bool("skip-wheels", false, "do not build the wheelhouse (it needs a package index)")
	volume := fs.String("models-volume", defaultVolume, "the ollama model volume")
	helper := fs.String("helper-image", defaultHelper, "image used to read the model volume")
	python := fs.String("python-image", defaultPython, "image the wheelhouse is resolved against")
	created := fs.String("created", "", "RFC3339 timestamp; pin it to make a rebuild byte-identical")
	keep := fs.Bool("keep-staging", false, "do not delete the staging directory afterwards")
	images := fs.String("images", "", "comma-separated image refs (default: the stack)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *out == "" {
		return fmt.Errorf("create needs -o <file>")
	}
	refs := defaultImages
	if *images != "" {
		refs = nil
		for _, r := range strings.Split(*images, ",") {
			if r = strings.TrimSpace(r); r != "" {
				refs = append(refs, r)
			}
		}
	}
	// The helper image writes the model volume at restore, so a bundle without
	// it fails on exactly the kind of host it exists for: one with docker and
	// nothing else to unpack a tar into a volume with.
	if !contains(refs, *helper) {
		return fmt.Errorf(
			"the helper image %s is not in --images.\n"+
				"  It is what writes the model volume at restore, and a bundle that needs\n"+
				"  a tool it does not carry is not a bundle.", *helper)
	}
	if *staging == "" {
		*staging = *out + ".staging"
	}
	// Absolute, because the wheelhouse step bind-mounts it: `docker run -v` reads
	// a relative source as a NAMED VOLUME, not a directory, and fails with a
	// complaint about invalid characters in a volume name rather than anything
	// resembling "that path is relative".
	absStaging, err := filepath.Abs(*staging)
	if err != nil {
		return err
	}
	*staging = absStaging
	abs, err := filepath.Abs(*repo)
	if err != nil {
		return err
	}

	spec := bundle.CreateSpec{
		Repo: abs, Out: *out, Staging: *staging,
		Images: refs, ModelsVolume: *volume,
		HelperImage: *helper, PythonImage: *python,
		All: *all, SkipWheels: *skipWheels, Created: *created,
		Tool: "nightglass-bundle " + Version,
		Out2: os.Stdout, TTY: isTTY(),
	}
	m, digest, err := bundle.Create(spec)
	if err != nil {
		return err
	}
	if !*keep {
		os.RemoveAll(*staging)
	}

	st, _ := os.Stat(*out)
	fmt.Printf("\n\033[1m%s\033[0m\n%s\n", *out, strings.Repeat("-", 60))
	fmt.Printf("   %-24s %s bytes\n", "archive", commas(st.Size()))
	fmt.Printf("   %-24s %d\n", "entries", m.Totals.Entries)
	fmt.Printf("   %-24s %s\n", "manifest sha256", digest)
	fmt.Printf("\n   That digest is the one value to carry out of band. It is what `verify`\n")
	fmt.Printf("   prints back, and it is integrity, not authenticity — it says this is the\n")
	fmt.Printf("   bundle that was built, not who built it.\n")
	return nil
}

// -- verify ------------------------------------------------------------------

func cmdVerify(args []string) error {
	fs := flag.NewFlagSet("verify", flag.ExitOnError)
	verbose := fs.Bool("v", false, "a line per entry")
	path, err := parseOnePositional(fs, args, "verify needs one bundle, or - for stdin")
	if err != nil {
		return err
	}

	r, size, closer, err := open(path)
	if err != nil {
		return err
	}
	defer closer()

	fmt.Printf("\n\033[1mverify\033[0m %s\n%s\n", path, strings.Repeat("-", 60))
	res, err := bundle.Verify(r, bundle.Options{
		Verbose: *verbose, Progress: os.Stdout, TTY: isTTY(),
	})
	if err != nil {
		return err
	}

	fmt.Printf("\n   %-24s %d\n", "entries", res.Entries)
	fmt.Printf("   %-24s %s\n", "bytes", commas(res.Bytes))
	if size > 0 {
		fmt.Printf("   %-24s %s\n", "archive on disk", commas(size))
	}
	for _, k := range []manifest.Kind{
		manifest.KindImage, manifest.KindModelBlob, manifest.KindModelManifest,
		manifest.KindWheel, manifest.KindData,
	} {
		if st := res.ByKind[k]; st != nil {
			fmt.Printf("   %-24s %3d  %s\n", "  "+string(k), st.Entries, commas(st.Bytes))
		}
	}
	fmt.Printf("   %-24s %s\n", "manifest sha256", res.ManifestSHA256)
	fmt.Printf("   %-24s %s  (%s)\n", "built", res.Manifest.Created, res.Manifest.Tool)
	if c := res.Manifest.Source.GitCommit; c != "" {
		dirty := ""
		if res.Manifest.Source.GitDirty {
			dirty = " (working tree dirty)"
		}
		fmt.Printf("   %-24s %s%s\n", "from commit", c, dirty)
	}
	fmt.Printf("\n\033[1mOK\033[0m  every entry matched, and every entry the manifest lists was present.\n")
	return nil
}

// -- restore -----------------------------------------------------------------

func cmdRestore(args []string) error {
	fs := flag.NewFlagSet("restore", flag.ExitOnError)
	into := fs.String("into", "", "directory to unpack into (required)")
	repo := fs.String("repo", "", "the clone the data half is placed in (needed with --install)")
	install := fs.Bool("install", false, "also docker load, fill the model volume and place the data")
	volume := fs.String("models-volume", defaultVolume, "the ollama model volume")
	helper := fs.String("helper-image", defaultHelper, "image used to write the model volume")
	verbose := fs.Bool("v", false, "a line per entry")
	path, err := parseOnePositional(fs, args, "restore needs one bundle, or - for stdin")
	if err != nil {
		return err
	}
	if *repo != "" {
		abs, err := filepath.Abs(*repo)
		if err != nil {
			return err
		}
		*repo = abs
	}

	r, _, closer, err := open(path)
	if err != nil {
		return err
	}
	defer closer()

	fmt.Printf("\n\033[1mrestore\033[0m %s\n%s\n", path, strings.Repeat("-", 60))
	_, err = bundle.Restore(r, bundle.RestoreSpec{
		Into: *into, Repo: *repo, ModelsVolume: *volume, HelperImage: *helper,
		Install: *install, Log: os.Stdout,
		Opt: bundle.Options{Verbose: *verbose, Progress: os.Stdout, TTY: isTTY()},
	})
	return err
}

// -- inspect -----------------------------------------------------------------

func cmdInspect(args []string) error {
	fs := flag.NewFlagSet("inspect", flag.ExitOnError)
	asJSON := fs.Bool("json", false, "print the manifest verbatim")
	path, err := parseOnePositional(fs, args, "inspect needs one bundle")
	if err != nil {
		return err
	}
	r, _, closer, err := open(path)
	if err != nil {
		return err
	}
	defer closer()

	raw, m, err := bundle.ReadManifest(r)
	if err != nil {
		return err
	}
	if *asJSON {
		os.Stdout.Write(raw)
		return nil
	}

	fmt.Printf("\n\033[1m%s\033[0m\n%s\n", path, strings.Repeat("-", 60))
	fmt.Printf("   %-24s %s\n", "format", m.Format)
	fmt.Printf("   %-24s %s\n", "built", m.Created)
	fmt.Printf("   %-24s %s\n", "tool", m.Tool)
	if m.Source.GitCommit != "" {
		fmt.Printf("   %-24s %s\n", "commit", m.Source.GitCommit)
	}
	fmt.Printf("   %-24s %d, %s bytes\n", "entries", m.Totals.Entries, commas(m.Totals.Bytes))
	fmt.Println()
	for _, e := range m.Entries {
		if e.Kind == manifest.KindImage {
			fmt.Printf("   %-14s %-46s %14s\n", e.Kind, e.Image.Ref, commas(e.Bytes))
		}
	}
	byKind := map[manifest.Kind]int{}
	byKindBytes := map[manifest.Kind]int64{}
	for _, e := range m.Entries {
		byKind[e.Kind]++
		byKindBytes[e.Kind] += e.Bytes
	}
	for _, k := range []manifest.Kind{
		manifest.KindModelBlob, manifest.KindModelManifest,
		manifest.KindWheel, manifest.KindData,
	} {
		if byKind[k] > 0 {
			fmt.Printf("   %-14s %-46s %14s\n",
				k, fmt.Sprintf("%d files", byKind[k]), commas(byKindBytes[k]))
		}
	}
	fmt.Printf("\n   Nothing above was verified — inspect reads the first member and stops.\n")
	fmt.Printf("   `verify` is what checks the bytes.\n")
	return nil
}

// -- plumbing ----------------------------------------------------------------

// parseOnePositional accepts flags on either side of the file argument.
//
// Go's flag package stops at the first non-flag token, so `verify bundle.tar
// -v` silently drops the -v and `restore b.tar --into x --install` drops
// everything. Both read as the obvious way to type the command, and the
// failure is a flag quietly not applying — which for --install would mean a
// restore that verified and then did nothing, reported as success.
func parseOnePositional(fs *flag.FlagSet, args []string, what string) (string, error) {
	var pos string
	for {
		if err := fs.Parse(args); err != nil {
			return "", err
		}
		if fs.NArg() == 0 {
			break
		}
		if pos != "" {
			return "", fmt.Errorf("unexpected extra argument %q", fs.Arg(0))
		}
		pos = fs.Arg(0)
		args = fs.Args()[1:]
	}
	if pos == "" {
		return "", fmt.Errorf("%s", what)
	}
	return pos, nil
}

// open returns a reader for a path or for stdin.
//
// The stdin case is the one that matters: it lets a bundle be verified as it
// streams off removable media, which over 18 GB is the difference between a
// tool someone runs and one they avoid.
func open(path string) (r *bufio.Reader, size int64, closer func(), err error) {
	if path == "-" {
		return bufio.NewReaderSize(os.Stdin, 1<<20), 0, func() {}, nil
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, 0, nil, err
	}
	st, err := f.Stat()
	if err != nil {
		f.Close()
		return nil, 0, nil, err
	}
	return bufio.NewReaderSize(f, 1<<20), st.Size(), func() { f.Close() }, nil
}

// isTTY decides whether progress is a bar or a log.
//
// A carriage return is a progress bar on a terminal and a wall of repeated
// lines in a recording — the same reason src/nightglass/spatial/archive.py
// branches on it. Done with a stat rather than golang.org/x/term because one
// dependency for one bit is a poor trade in a binary whose selling point is
// that it has none.
func isTTY() bool {
	st, err := os.Stdout.Stat()
	if err != nil {
		return false
	}
	return st.Mode()&os.ModeCharDevice != 0
}

func contains(xs []string, x string) bool {
	for _, s := range xs {
		if s == x {
			return true
		}
	}
	return false
}

func commas(n int64) string {
	s := fmt.Sprintf("%d", n)
	var out []byte
	for i, c := range []byte(s) {
		if i > 0 && (len(s)-i)%3 == 0 {
			out = append(out, ',')
		}
		out = append(out, c)
	}
	return string(out)
}
