// Minimal ATS-safe CV template. See PROJECT.md §8.
//
// Reads structured content from a JSON file (path passed via
// `typst compile --input data_path=...`, read with `#sys.inputs.data_path`)
// -- content and layout are separate so the design can change later without
// touching the pipeline. Syntax verified live against
// https://typst.app/docs on 2026-09-16 for the installed typst 0.15.1:
// json(path) reads a file directly; #sys.inputs.<key> is always a string;
// #set text(ligatures: false) disables the OpenType liga/clig features.
//
// No tables, columns, icons, photo, date of birth, or street address.
// Single column, A4, contact info as text links in the body (not a
// header/footer, which some ATS parsers drop).

#let data = json(sys.inputs.data_path)

#set page(paper: "a4", margin: 2cm)
#set text(font: "DejaVu Sans", size: 10.5pt, ligatures: false, lang: "en")
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

#v(0.5em)

= Summary
#data.summary

= Technical Skills
#data.skills.join(", ")

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

= Languages
#for l in data.languages [
  #l.name: #l.cefr #h(1.5em)
]
