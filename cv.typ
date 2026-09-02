#set page(
  paper: "a4",
  margin: (x: 1.6cm, y: 1.4cm),
)

#set text(
  font: "FiraCode Nerd Font Mono",
  10.5pt,
  fill: rgb("#1f2933"),
)

#set par(
  leading: 0.65em,
  justify: true,
)

#set heading(numbering: none)

#show heading.where(level: 1): it => block(above: 0pt, below: 0.15em)[
  #set align(center)
  #set text(20pt, weight: "bold", fill: rgb("#0f172a"))
  #it.body
]

#show heading.where(level: 2): it => block(above: 1.2em, below: 0.55em)[
  #set text(11.5pt, weight: "bold", fill: rgb("#0f172a"))
  #it.body
  #v(0.2em)
  #line(length: 100%, stroke: (paint: rgb("#94a3b8"), thickness: 0.7pt))
]

#show heading.where(level: 3): it => block(above: 0.95em, below: 0.18em)[
  #set text(10.7pt, weight: "bold", fill: rgb("#111827"))
  #it.body
]

#show heading.where(level: 4): it => block(above: 0pt, below: 0.45em)[
  #set text(9.6pt, fill: rgb("#475569"))
  #it.body
]

#show link: set text(fill: rgb("#0f766e"))
#show link: underline

= Ivan Kirilov Dimitrov
== Software Developer
#show link: set text(10pt)

#link("mailto:ivan@idimitrov.dev")[ivan\@idimitrov.dev] |
#link("https://github.com/ivandimitrov8080")[github.com/ivandimitrov8080] |
#link("https://www.upwork.com/freelancers/idimitrov")[upwork.com/idimitrov]

#show link: set text(11pt)
== Experience

=== IDimitrov Ltd., Director
==== Blagoevgrad, Bulgaria — 2023–Present

My software consultancy engaging in enterprise web development.

#link("https://www.upwork.com/freelancers/idimitrov")[Upwork] | #link("https://idimitrov.dev/")[Resume]

=== Deutsche Börse AG, Senior Software Developer
==== Prague, Czechia — Feb 2024–Dec 2025

Data-driven Spring Framework maven project integrating with SAP, GLEIF and
other, internal, services.
The react frontend used redux and RTK query for data display and bootstrap for
styles.

#link("https://www.eurex.com/ec-en/support/initiatives/c7-client-administration-service/C7-CAS-Releases-2900566?frag=3919554")[Release notes]

=== RA Creative, Software Developer
==== Nottingham, UK — Dec 2020–Jan 2023

SAP hybris project (based on Spring+Maven+Ant with many extensions) integrating with
Adyen, PayPal, WellsFargo, ParcelLab among others. The frontend was a mix of vanilla html,css,js,thymeleaf and react.

#link("https://racreative.co.uk/")[RA Creative] |
#link("https://idimitrov.dev/posts/my-work/parcellab.html")[Parcel Lab case study] |
#link("https://idimitrov.dev/posts/my-work/wellsfargo.html")[Wells Fargo case study]

=== Central Net, Full Stack + Mobile Software Developer
==== Blagoevgrad, Bulgaria — May 2016–May 2020

Spring Framework project with a react web frontend and an android app.

== Education

=== SWU 'Neofit Rilski', Bachelor's in Electronics
==== Blagoevgrad, Bulgaria — Sep 2016–Jun 2018

== Certificates

#link("https://www.credly.com/badges/281fbd5f-ca29-4235-b023-a9b93af2f6c5/public_url")[Oracle Certified Professional, Java SE 8 Programmer - Issued by Oracle — 19 May 2020]

#link("https://www.credly.com/badges/910f311b-0f7f-4911-b945-5ded663408ec/public_url")[Oracle Certified Associate, Java SE 8 Programmer - Issued by Oracle — 17 Mar 2020]
