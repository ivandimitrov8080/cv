#set page(
  paper: "a4",
  margin: (x: 1.6cm, y: 1.4cm),
)

#set text(
  font: "Times New Roman",
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
#link("tel:+31685743745")[+31 685 743 745] |
#link("https://github.com/ivandimitrov8080")[github.com/ivandimitrov8080] |
#link("https://www.upwork.com/freelancers/idimitrov")[upwork.com/idimitrov]

#show link: set text(11pt)
== Summary

Software developer with 8+ years of experience across enterprise Java systems,
eCommerce integrations, and modern web applications. Reduced GLEIF import time
by 97% (5 hours to 10 minutes) and halved CI build time at Deutsche Börse AG,
and led a Reporting ID redesign that cut production defects. Founded and ran a
solo consultancy delivering 15 projects for 10 clients, and integrated
providers including SAP, GLEIF, Google, Wells Fargo, Adyen, PayPal, and
parcelLab.

== Skills

*Languages:* Java, JavaScript, TypeScript, SQL, Bash, Python

*Backend:* Spring Framework, SAP Commerce / Hybris, Node.js, Express, Next.js

*Frontend:* React, Redux, RTK Query, Bootstrap, TailwindCSS, DaisyUI,
Thymeleaf, HTML, CSS, user interface (UI) and user experience (UX) design

*Database management:* PostgreSQL, OracleDB

*Cloud / Infra:* cloud computing, Docker, Kubernetes (K8S), minikube, Vercel,
NixOS, Linux

*Build / Package:* Maven, Gradle, Ant, Webpack, Vite

*Integrations:* SAP, GLEIF, Google Drive and Docs APIs, Wells Fargo Open
Banking, parcelLab, Adyen, PayPal

*APIs:* REST, SOAP, tRPC

*Software testing:* JUnit 4/5/6, Mockito, Postman, Hurl, debugging

*Software maintenance and technical documentation:* JavaDocs, Swagger

*Mobile:* mobile development, cross-platform development (PWA),
Android (Java)

*Practices:* software development, DevOps practices, Git version control,
code refactoring, code review, data structures and algorithms, security best
practices, technical support, business analysis, requirements analysis,
Agile/Scrum, problem solving

== Experience

=== IDimitrov Ltd., Director
==== Blagoevgrad, Bulgaria — 2023–Present

Software consultancy focused on enterprise web development, integrations,
internal tooling, and custom web applications.

- Founded and ran a solo software consultancy, delivering 15 projects for 10
  clients since 2023, including repeat engagements.
- Rated 5.0/5.0 by clients across 6 reviews on Upwork, and solely responsible
  for every stage of delivery on all 15 projects — requirements analysis,
  architecture, implementation, deployment, and client communication.
- Built a multi-tenant knowledge base web app using Next.js, TypeScript,
  TailwindCSS, DaisyUI, PostgreSQL, Prisma, NextAuth, and Google Drive APIs,
  hosted on Vercel.
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

- Reduced GLEIF import time by 97%, from 5 hours to 10 minutes, by optimizing
  the Spring/Maven data import pipeline.
- Redesigned the Reporting ID functionality so data from multiple Disclosed
  Clients consolidates into a single report — including across different
  Clearing Members, which was not possible before. Introduced the OTC Report
  Receiver admission type with consolidated OTC daily reports (CC203, CC204,
  CC209) and 5-letter Reporting ID setup via C7 CAS (Release 2.293), cutting
  production defects versus the previous process.
- Delivered OTC IRS Products & Currencies maintenance, moving Clearing Members
  from paper-based setup requests to paperless self-service management of OTC
  IRS products and currencies via C7 CAS (Release 2.284.6, production 30
  September 2024) across Eurex Clearing's ~200 Clearing Members in 22
  countries.
- Enabled same-day submission of Segregation Change Requests and Position
  Account Requests that depend on not-yet-synced SAP data, removing a day of
  waiting for users.
- Cut CI build time by 50% using Maven parallel builds while keeping automated
  code quality checks in the pipeline; reviewed code and refactored modules to
  maintain code quality and standards.
- Led API development for third-party service integrations and documented
  interfaces and APIs for consuming teams.
- Introduced a senior developer to the project by telling him what I know,
  increasing team capacity on the project and shortening ramp-up through
  documented interfaces and setup notes.

#link("https://www.eurex.com/ec-en/support/initiatives/c7-client-administration-service/C7-CAS-Releases-2900566?frag=3919554")[Release notes] | #link("https://idimitrov.dev/posts/my-work/c7cas.html")[Project notes]

=== RA Creative, Software Developer
==== Nottingham, UK — Dec 2020–Jan 2023

Worked on SAP Commerce / Hybris projects for international eCommerce brands in
an agency environment delivering design, development, and integrations.

- Delivered SAP Commerce / Hybris solutions for Watches of Switzerland Group
  across five brands (Mayors, Mappin & Webb, Watches of Switzerland UK/US,
  Goldsmiths), on an estate with £1.2 billion group revenue and seven retail
  websites (FY22).
- Integrated parcelLab across three of the group's brands (Watches of
  Switzerland UK, Mappin & Webb and Goldsmiths), introducing branded
  order-status tracking pages and proactive delivery and returns notifications
  where none existed before, across 130+ stores with an average order value
  over £5,900.
- Integrated payment and commerce providers including Adyen, PayPal,
  Wells Fargo Open Banking, and parcelLab into production storefront, checkout
  and customer-account workflows.
- Built and maintained full-stack Spring, Maven, and Ant-based SAP Commerce
  solutions across storefront, backend, integration, and frontend layers,
  including vanilla HTML, CSS, JavaScript, Thymeleaf, and React.
- Integrated a DevOps watchdog service that automatically restarted a
  crash-prone internal application, cutting crashes by ~99% (from about one a
  week to near zero, near-100% uptime) and removing manual intervention.
- Tested releases and reviewed code to maintain quality in a 4-developer team.

#link("https://racreative.co.uk/")[RA Creative] |
#link("https://idimitrov.dev/posts/my-work/parcellab.html")[Parcel Lab case study] |
#link("https://idimitrov.dev/posts/my-work/wellsfargo.html")[Wells Fargo case study]

=== Central Net, Full Stack + Mobile Software Developer
==== Blagoevgrad, Bulgaria — May 2016–May 2020

Worked on a Spring Framework product with a React web frontend and Android app.

- Delivered features across backend, web, and mobile layers of a product used
  by 100+ technical students to track homework and news and vote on exam dates,
  sustaining the product over four years (2016–2020).
- Developed full-stack functionality spanning Java/Spring services, React UI,
  and Android client code (mobile development) in a small team, owning delivery
  from implementation through testing.
- Tested software across web and mobile platforms to ensure quality.

== Education

=== SWU 'Neofit Rilski', Bachelor's in Electronics
==== Blagoevgrad, Bulgaria — Sep 2016–Jun 2018

== Certificates

#link("https://www.credly.com/badges/281fbd5f-ca29-4235-b023-a9b93af2f6c5/public_url")[Oracle Certified Professional, Java SE 8 Programmer - Issued by Oracle — 19 May 2020]

#link("https://www.credly.com/badges/910f311b-0f7f-4911-b945-5ded663408ec/public_url")[Oracle Certified Associate, Java SE 8 Programmer - Issued by Oracle — 17 Mar 2020]
