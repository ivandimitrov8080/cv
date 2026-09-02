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

#set list(spacing: 0.28em)

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
== Summary

Software developer with experience across enterprise Java systems, eCommerce
integrations, and modern web applications. Worked on Spring and SAP Commerce
projects, React frontends, and API-heavy products integrating with providers
including SAP, GLEIF, Google, Wells Fargo, Adyen, PayPal, and parcelLab.

== Skills

*Languages:* Java, JavaScript, TypeScript, Haskell, Elm, SQL

*Backend:* Spring Framework, Maven, REST APIs, SAP Commerce / Hybris,
Next.js server features, NextAuth

*Frontend:* React, Redux, RTK Query, Bootstrap, TailwindCSS, DaisyUI,
Thymeleaf, HTML, CSS

*Data / Infra:* PostgreSQL, Prisma ORM, Nix, NixOS, Linux, JMX / VisualVM

*Integrations:* SAP, GLEIF, Google Drive and Docs APIs, Wells Fargo Open
Banking, parcelLab, Adyen, PayPal

== Experience

=== IDimitrov Ltd., Director
==== Blagoevgrad, Bulgaria — 2023–Present

Software consultancy focused on enterprise web development, integrations,
internal tooling, and custom web applications.

- Built a multi-tenant knowledge base web app using Next.js, TypeScript,
  TailwindCSS, DaisyUI, PostgreSQL, Prisma, NextAuth, and Google Drive APIs.
- Implemented document rendering and permissions-aware access for Google Docs,
  Sheets, Slides, PDFs, and Drive files, with performance optimizations for
  API-heavy request flows.
- Delivered documentation-oriented websites and supporting infrastructure,
  including a static Markdown wiki and a Hakyll-based site deployed on NixOS.

#link("https://www.upwork.com/freelancers/idimitrov")[Upwork] | #link("https://idimitrov.dev/")[Portfolio] | #link("https://idimitrov.dev/posts/my-work/stepsy-wiki.html")[Knowledge base case study]

=== Deutsche Börse AG, Senior Software Developer
==== Prague, Czechia — Feb 2024–Dec 2025

Worked on a data-driven Spring Framework Maven application integrating with
SAP, GLEIF, and internal services, with a React frontend using Redux,
RTK Query, and Bootstrap.

- Delivered release-backed features for C7 CAS, including workflows for OTC
  IRS product and currency activation and deactivation.
- Contributed to the Reporting ID redesign by supporting the OTC Report
  Receiver admission type and related account-linking flows.
- Helped replace paper-based administration with digital request handling in a
  regulated enterprise environment.

#link("https://www.eurex.com/ec-en/support/initiatives/c7-client-administration-service/C7-CAS-Releases-2900566?frag=3919554")[Release notes] | #link("https://idimitrov.dev/posts/my-work/c7cas.html")[Project notes]

=== RA Creative, Software Developer
==== Nottingham, UK — Dec 2020–Jan 2023

Worked on SAP Commerce / Hybris projects for international eCommerce brands in
an agency environment delivering design, development, and integrations.

- Built and maintained Spring, Maven, and Ant-based SAP Commerce solutions
  across storefront, backend, and integration layers.
- Integrated payment and commerce providers including Adyen, PayPal,
  Wells Fargo Open Banking, and parcelLab.
- Contributed to frontend implementations across vanilla HTML, CSS,
  JavaScript, Thymeleaf, and React.
- Supported international retail eCommerce clients through secure payments,
  post-purchase, and customer account workflows.

#link("https://racreative.co.uk/")[RA Creative] |
#link("https://idimitrov.dev/posts/my-work/parcellab.html")[Parcel Lab case study] |
#link("https://idimitrov.dev/posts/my-work/wellsfargo.html")[Wells Fargo case study]

=== Central Net, Full Stack + Mobile Software Developer
==== Blagoevgrad, Bulgaria — May 2016–May 2020

Worked on a Spring Framework product with a React web frontend and Android app.

- Delivered features across backend, web, and mobile layers in a small team.
- Contributed to full-stack development spanning Java services, React UI, and
  Android client functionality.

== Education

=== SWU 'Neofit Rilski', Bachelor's in Electronics
==== Blagoevgrad, Bulgaria — Sep 2016–Jun 2018

== Certificates

#link("https://www.credly.com/badges/281fbd5f-ca29-4235-b023-a9b93af2f6c5/public_url")[Oracle Certified Professional, Java SE 8 Programmer - Issued by Oracle — 19 May 2020]

#link("https://www.credly.com/badges/910f311b-0f7f-4911-b945-5ded663408ec/public_url")[Oracle Certified Associate, Java SE 8 Programmer - Issued by Oracle — 17 Mar 2020]
