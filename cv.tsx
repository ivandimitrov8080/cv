import {
  Page,
  Text,
  View,
  Document,
} from "@react-pdf/renderer";
import ReactPDF from "@react-pdf/renderer";
import fs from "fs";
import Experience from "./theme/experience";
import Education from "./theme/education";
import Certificate from "./theme/certificate";
import { svg, tw } from "./theme/lib";
import SvgLink from "./theme/link";

const experience = ({
  company,
  position,
  location,
  from,
  to,
  description,
  links,
  feedback,
}: any) => (
  <Experience company={company} position={position} location={location} from={from} to={to} description={description} links={links} feedback={feedback} />
);

const education = ({
  institution,
  location,
  degree,
  field,
  from,
  to,
  summary,
  links,
}: any) => (
  <Education institution={institution} location={location} degree={degree} field={field} from={from} to={to} summary={summary} links={links} />
);

const certificate = ({
  name,
  issuer,
  description,
  date,
  links,
}: any) => (
  <Certificate name={name} issuer={issuer} description={description} date={date} links={links} />
);

const Links = () => (
  <View style={tw("flex flex-row gap-4 w-full text-sm justify-center p-4")}>
    <SvgLink text="GitHub" href="https://github.com/ivandimitrov8080" icon={svg.github as any} />
    <SvgLink text="Upwork" href="https://www.upwork.com/freelancers/idimitrov" icon={svg.link as any} />
    <SvgLink text="ivan@idimitrov.dev" href="mailto:ivan@idimitrov.dev" icon={svg.email as any} />
    <SvgLink text="idimitrov.dev" href="https://www.idimitrov.dev" icon={svg.globe as any} />
  </View>
);

const Intro = () => (
  <View style={tw("text-center border-2 border-slate-50 rounded-full")}>
    <Text style={tw("text-5xl")}>Ivan K. Dimitrov</Text>
    <Text>Software Developer</Text>
    <Links />
  </View>
);

const pageStyles = tw(
  "w-full h-full text-slate-50 bg-gray-900 flex flex-col p-12 text-base"
);
const divider = (
  <View style={tw("w-full mt-4")}>
    <View style={tw("w-full border-slate-50 border-b-[.2px]")}></View>
  </View>
);

const CV = () => (
  <Document
    title="CV"
    author="Ivan Kirilov Dimitrov"
    subject="My professional resume"
    creator="Ivan Dimitrov with react-pdf"
    producer="Ivan Dimitrov with react-pdf"
    keywords="Ivan Dimitrov Software Developer"
  >
    <Page size="A4" style={pageStyles}>
      <Intro />
      {divider}
      <Text style={tw("text-2xl mt-2 text-violet-500")}>Experience</Text>
      <View style={tw("my-auto")}>
        {experience({
          company: "idimitrov.dev",
          position: "Software Developer / Owner",
          location: "Worldwide",
          from: new Date("2023"),
          to: new Date("9999"),
          description:
            "This is my software consulting and development business. It offers web development services to businesses around the world. Please head over to my resume website or my Upwork profile to learn more.",
          links: [
            {
              text: "Upwork",
              href: "https://www.upwork.com/freelancers/idimitrov",
            },
            {
              text: "Resume",
              href: "https://www.idimitrov.dev/cases",
            },
          ],
          feedback: "100% Job Success"
        })}
        {experience({
          company: "Stepsy",
          position: "Freelance Full Stack Software Developer",
          location: "Estonia",
          from: new Date("29 Jul 2023"),
          to: new Date("5 Nov 2023"),
          description:
            "As a software developer working with stepsy.co, I was responsible for implementing their brand new wiki web app stepsy.wiki. Working on this greenfield project allowed me to make fundamental technical decisions that had a positive impact on further development.",
          links: [
            {
              text: "Case Study",
              href: "https://www.idimitrov.dev/c/cases/stepsy.wiki.md",
            },
          ],
          feedback:
            "Great experience working with Ivan! Ready to implement your vision, also advises on how it should be done.",
        })}
        {experience({
          company: "RA Creative",
          position: "Full Stack Software Developer",
          location: "Nottingham, UK",
          from: new Date("Dec 2020"),
          to: new Date("20 Jan 2023"),
          description:
            "As a software developer at RA Creative, I was responsible for delivering software solutions to an eCommerce business operating in 2 continents - Europe and North America. Watches of Switzerland Group is an international retailer of world leading luxury watch and jewellery brands. It has a market cap of £1.5B.",
          links: [
            { text: "RA Creative", href: "https://racreative.co.uk/" },
            {
              text: "Parcel Lab case study",
              href: "https://www.idimitrov.dev/c/cases/parcellab.md",
            },
            {
              text: "Wells Fargo case study",
              href: "https://www.idimitrov.dev/c/cases/wellsfargo.md",
            },
          ],
        })}
        {experience({
          company: "Central Net",
          position: "Full Stack + Mobile Software Developer",
          location: "Blagoevgrad, Bulgaria",
          from: new Date("May 2016"),
          to: new Date("May 2020"),
          description:
            "Developed a full-stack web + android app helping students book exams, browse resources, see events, news and more.",
        })}
        {divider}
        <Text style={tw("text-2xl mt-2 text-violet-500")}>Education</Text>
        {education({
          institution: "SWU 'Neofit Rilski'",
          location: "Blagoevgrad, Bulgaria",
          degree: "Bachelor's",
          field: "Electronics",
          from: new Date("Sep 2016"),
          to: new Date("Jun 2018"),
          summary:
            "This is an engineering degree focused on the science of electronics and electrical engineering. It studies the physical properties of individual electrons and the forces that take place when current is flowing through a circuit.",
        })}
        {divider}
        <Text style={tw("text-2xl mt-2 text-violet-500")}>Certificates</Text>
        {certificate({
          name: "Oracle Certified Professional, Java SE 8 Programmer",
          issuer: "Oracle",
          description:
            "An Oracle Certified Professional, Java SE 8 Programmer has validated their Java development skills by answering challenging, real-world, scenario-based questions that measure problem solving skills using Java code.",
          date: new Date("19 May 2020"),
          links: [
            {
              text: "Credly",
              href: "https://www.credly.com/badges/281fbd5f-ca29-4235-b023-a9b93af2f6c5/public_url",
            },
          ],
        })}
        {certificate({
          name: "Oracle Certified Associate, Java SE 8 Programmer",
          issuer: "Oracle",
          description:
            "An Oracle Certified Associate, Java SE 8 Programmer has demonstrated knowledge of object-oriented concepts, the Java programming language and general knowledge of Java platforms and technologies.",
          date: new Date("17 Mar 2020"),
          links: [
            {
              text: "Credly",
              href: "https://www.credly.com/badges/910f311b-0f7f-4911-b945-5ded663408ec/public_url",
            },
          ],
        })}
      </View>
    </Page>
  </Document>
);

const outDir = process.env.out || "./";
const pname = process.env.pname || "cv";

if (!fs.existsSync(outDir)) {
  fs.mkdirSync(outDir, { recursive: true });
}

ReactPDF.render(<CV />, `${outDir}/${pname}.pdf`);
