// Minimal ATS-safe CV template. See PROJECT.md §8.
//
// Reads structured content from a JSON file (path passed via
// `typst compile --input data_path=...`, read with `#sys.inputs.data_path`)
// -- content and layout are separate so the design can change later without
// touching the pipeline. Syntax verified live against
// https://typst.app/docs on 2026-09-16 for the installed typst 0.15.1:
// json(path) reads a file directly; #sys.inputs.<key> is always a string;
// #set text(ligatures: false) disables the OpenType liga/clig features.
// kerning: false verified live 2026-09-24 (real end-to-end pipeline check):
// without it, DejaVu Sans's kerning after a capital "T" makes pypdf's text
// extraction insert a stray space ("T elegram", "DevT ools").
//
// No tables, columns, icons, photo, date of birth, or street address.
// Single column, A4, contact info as text links in the body (not a
// header/footer, which some ATS parsers drop).

#let data = json(sys.inputs.data_path)

#set page(paper: "a4", margin: 2cm)
// kerning: false is required, not just ligatures: false -- verified live
// 2026-09-24: DejaVu Sans's default kerning after a capital "T" (e.g.
// "Telegram", "Took", "DevTools") produces a glyph gap pypdf's text
// extraction misreads as a word space ("T elegram", "DevT ools"), which
// would silently break ATS keyword matching on submitted applications.
#set text(font: "DejaVu Sans", size: 10.5pt, ligatures: false, kerning: false, lang: "en")
#set par(justify: false, leading: 0.65em)
#set heading(numbering: none)
#show heading: set text(size: 12pt, weight: "bold")
#show heading: it => [
  #v(0.6em)
  #it.body
  #v(0.2em)
  #line(length: 100%, stroke: 0.5pt)
]

#align(center)[
  #text(size: 16pt, weight: "bold")[#data.name]
]

#align(center)[
  #data.contact.city, #data.contact.country #sym.dot #data.contact.timezone \
  #link("mailto:" + data.contact.email)[#data.contact.email] #sym.dot #data.contact.phone
  #if data.contact.linkedin != "" [ #sym.dot #link(data.contact.linkedin)[LinkedIn] ]
  #if data.contact.github != "" [ #sym.dot #link(data.contact.github)[GitHub] ]
]

#v(0.3em)

#align(center)[#text(weight: "medium")[#data.headline]]

#v(0.3em)

= Technical Skills
#for group in data.skills [
  *#group.category:* #group.items.join(", ") \
]

= Projects
#for p in data.projects [
  *#p.name* #h(1fr) #p.dates \
  #if p.summary != "" [#p.summary \ ]
  #for b in p.bullets [ #sym.bullet #b \ ]
  #v(0.4em)
]

= Experience
#for e in data.experience [
  *#e.title*, #e.employer #h(1fr) #e.dates \
  #for b in e.bullets [ #sym.bullet #b \ ]
  #v(0.4em)
]

= Education
#for ed in data.education [
  *#ed.institution* --- #ed.degree #h(1fr) #ed.dates \
]
