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
#link("https://github.com/ivandimitrov8080")[github.com/ivandimitrov8080] |
#link("https://www.upwork.com/freelancers/idimitrov")[upwork.com/idimitrov]

#show link: set text(11pt)
== Summary

Software developer with 8+ years of experience across enterprise Java systems,
eCommerce integrations, and modern web applications. Reduced GLEIF import time
from 5 hours to 10 minutes and led a Reporting ID redesign that cut production
defects at Deutsche Börse AG. Delivered 15 projects for 10 clients as an
independent consultant and integrated providers including SAP, GLEIF, Google,
Wells Fargo, Adyen, PayPal, and parcelLab.

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
practices, technical support, business analysis, Agile/Scrum, problem solving

== Experience

=== IDimitrov Ltd., Director
==== Blagoevgrad, Bulgaria — 2023–Present

Software consultancy focused on enterprise web development, integrations,
internal tooling, and custom web applications.

- Delivered 15 projects for 10 clients as an independent software consultancy.
- Built a multi-tenant knowledge base web app using Next.js, TypeScript,
  TailwindCSS, DaisyUI, PostgreSQL, Prisma, NextAuth, and Google Drive APIs,
  hosted on Vercel.
- Implemented document rendering and permissions-aware access for Google Docs,
  Sheets, Slides, PDFs, and Drive files, with performance optimizations for
  API-heavy request flows.
- Delivered documentation-oriented websites and supporting infrastructure,
  including a static Markdown wiki and a Hakyll-based site deployed on NixOS.
- Operated as a solo consultancy, coordinating directly with each client's
  engineering, product, and design teams.

#link("https://www.upwork.com/freelancers/idimitrov")[Upwork] | #link("https://idimitrov.dev/")[Portfolio] | #link("https://idimitrov.dev/posts/my-work/stepsy-wiki.html")[Knowledge base case study]

=== Deutsche Börse AG, Senior Software Developer
==== Prague, Czechia — Feb 2024–Dec 2025

Worked on a data-driven Spring Framework Maven application integrating with
SAP, GLEIF, and internal services, with a React frontend using Redux,
RTK Query, and Bootstrap.

- Reported to the Head of Unit and Director, coordinating with project managers
  across delivery.
- Reduced GLEIF import time from 5 hours to 10 minutes by optimizing the data
  import pipeline.
- Redesigned the Reporting ID functionality that consolidates data from
  multiple Disclosed Clients into one report, introducing the OTC Report
  Receiver admission type with access to consolidated OTC daily reports
  (CC203, CC204, CC209) and 5-letter Reporting ID setup via C7 CAS, which
  reduced production defects and gave users a more flexible, streamlined
  process.
- Enabled same-day submission of Segregation Change Requests and Position
  Account Requests that depend on not-yet-synced SAP data, removing a day of
  waiting for users.
- Engineered release-backed features for C7 CAS, including OTC IRS product and
  currency activation and deactivation workflows.
- Led the transition from paper-based administration to digital request handling
  in a regulated enterprise environment.
- Led API development for third-party service integrations and documented
  interfaces and APIs for consuming teams.
- Established continuous integration pipelines for automated code quality checks
  and streamlined the software development lifecycle.
- Reviewed code and refactored modules to maintain code quality and standards.
- Recruited and onboarded a senior developer to the project.

#link("https://www.eurex.com/ec-en/support/initiatives/c7-client-administration-service/C7-CAS-Releases-2900566?frag=3919554")[Release notes] | #link("https://idimitrov.dev/posts/my-work/c7cas.html")[Project notes]

=== RA Creative, Software Developer
==== Nottingham, UK — Dec 2020–Jan 2023

Worked on SAP Commerce / Hybris projects for international eCommerce brands in
an agency environment delivering design, development, and integrations.

- Delivered SAP Commerce / Hybris solutions for Watches of Switzerland Group
  across five brands (Mayors, Mappin & Webb, Watches of Switzerland UK/US,
  Goldsmiths), supporting USD 1–2 billion in transaction volume.
- Built and maintained full-stack Spring, Maven, and Ant-based SAP Commerce
  solutions across storefront, backend, integration, and frontend layers,
  including vanilla HTML, CSS, JavaScript, Thymeleaf, and React.
- Integrated payment and commerce providers including Adyen, PayPal,
  Wells Fargo Open Banking, and parcelLab.
- Integrated a watchdog service that automatically restarted a crash-prone
  internal application, cutting crashes from about one a week to near zero
  (near-100% uptime) and removing manual intervention.
- Supported international retail eCommerce clients through secure payments,
  post-purchase, and customer account workflows.
- Collaborated within a team of 4 developers, 2 project managers, 1 tester, and
  1 designer.
- Tested releases and reviewed code to maintain quality.

#link("https://racreative.co.uk/")[RA Creative] |
#link("https://idimitrov.dev/posts/my-work/parcellab.html")[Parcel Lab case study] |
#link("https://idimitrov.dev/posts/my-work/wellsfargo.html")[Wells Fargo case study]

=== Central Net, Full Stack + Mobile Software Developer
==== Blagoevgrad, Bulgaria — May 2016–May 2020

Worked on a Spring Framework product with a React web frontend and Android app.

- Delivered features across backend, web, and mobile layers in a small team.
- Developed full-stack functionality spanning Java/Spring services, React UI,
  and Android client code (mobile development).
- Tested software across web and mobile platforms to ensure quality.

== Education

=== SWU 'Neofit Rilski', Bachelor's in Electronics
==== Blagoevgrad, Bulgaria — Sep 2016–Jun 2018

== Certificates

#link("https://www.credly.com/badges/281fbd5f-ca29-4235-b023-a9b93af2f6c5/public_url")[Oracle Certified Professional, Java SE 8 Programmer - Issued by Oracle — 19 May 2020]

#link("https://www.credly.com/badges/910f311b-0f7f-4911-b945-5ded663408ec/public_url")[Oracle Certified Associate, Java SE 8 Programmer - Issued by Oracle — 17 Mar 2020]
