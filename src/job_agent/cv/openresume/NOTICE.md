# Provenance

The `lib/` directory in this folder is a subset of the resume parser from
[xitanggg/open-resume](https://github.com/xitanggg/open-resume), licensed
under the GNU Affero General Public License v3.0 (see `LICENSE`). It is
used here, unmodified in algorithm, as a standalone Node script invoked by
`job render-test` (PROJECT.md §5.6 step 3) to cross-check the rendered CV
PDF's extracted fields.

Only the parser itself was vendored (`src/app/lib/parse-resume-from-pdf/`
and the two small runtime dependencies it pulls in, `deep-clone.ts` and
`lib/redux/resumeSlice.ts`) -- none of the original project's Next.js app,
UI, or resume-builder code is included.

## Changes made (per AGPL-3.0 §5's requirement to mark modified files)

The original code was written for pdfjs-dist 3.7.107 running inside a
browser/Next.js bundler. To run it headlessly under plain Node.js 22
(verified live 2026-09-16 against pdf.js's own
`examples/node/getinfo.mjs`), two files were adapted:

- `lib/parse-resume-from-pdf/read-pdf.ts`: replaced the browser
  worker/`pdfjs-dist/build/pdf.worker.entry` setup with
  `pdfjs-dist/legacy/build/pdf.mjs` (needs neither a worker nor the
  optional `canvas` package for text-only extraction), and changed
  `pdfjs.getDocument(fileUrl)` to `pdfjs.getDocument({ url: fileUrl })`
  (pdfjs-dist's current `getDocument` requires an options object).
- `lib/redux/resumeSlice.ts`: trimmed from the full Redux slice (which
  pulls in `@reduxjs/toolkit`, a CJS package that doesn't interop cleanly
  as an ESM named import in plain Node) down to the one constant the
  parser actually uses, `initialFeaturedSkills` -- same value, no
  behavior change.

Everything else (the actual section-grouping and field-extraction
heuristics) is unmodified.

`run-parser.ts` and `package.json`/`tsconfig.json` in this folder are new,
written for this project, to invoke the vendored parser as a CLI.
