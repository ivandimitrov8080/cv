import {
  Page,
  Text,
  View,
  Document,
  Link,
  Svg,
  Path,
} from "@react-pdf/renderer";
import ReactPDF from "@react-pdf/renderer";
import fs from "fs";
import { createTw } from "react-pdf-tailwind";
const tw = createTw({});

type A = {
  text: string;
  href: string;
  icon?: string[];
};

type Experience = {
  company: string;
  position: string;
  location: string;
  from: Date;
  to: Date;
  description: string;
  links?: A[];
  feedback?: string;
};

type Education = {
  institution: string;
  location: string;
  degree: string;
  field: string;
  from: Date;
  to: Date;
  summary: string;
  links?: A[];
};

type Certificate = {
  name: string;
  issuer: string;
  description: string;
  date: Date;
  links?: A[];
};

const createSvg = (paths: string[]) => (
  <Svg style={tw("w-4 h-4")} viewBox="0 0 19 19">
    {paths.map((p) => (
      <Path key={p} fill="#99f6e4" d={p} />
    ))}
  </Svg>
);

const svg = {
  github: createSvg([
    "M10 .333A9.911 9.911 0 0 0 6.866 19.65c.5.092.678-.215.678-.477 0-.237-.01-1.017-.014-1.845-2.757.6-3.338-1.169-3.338-1.169a2.627 2.627 0 0 0-1.1-1.451c-.9-.615.07-.6.07-.6a2.084 2.084 0 0 1 1.518 1.021 2.11 2.11 0 0 0 2.884.823c.044-.503.268-.973.63-1.325-2.2-.25-4.516-1.1-4.516-4.9A3.832 3.832 0 0 1 4.7 7.068a3.56 3.56 0 0 1 .095-2.623s.832-.266 2.726 1.016a9.409 9.409 0 0 1 4.962 0c1.89-1.282 2.717-1.016 2.717-1.016.366.83.402 1.768.1 2.623a3.827 3.827 0 0 1 1.02 2.659c0 3.807-2.319 4.644-4.525 4.889a2.366 2.366 0 0 1 .673 1.834c0 1.326-.012 2.394-.012 2.72 0 .263.18.572.681.475A9.911 9.911 0 0 0 10 .333Z",
  ]),
  link: createSvg([
    "M11.013 7.962a3.519 3.519 0 0 0-4.975 0l-3.554 3.554a3.518 3.518 0 0 0 4.975 4.975l.461-.46m-.461-4.515a3.518 3.518 0 0 0 4.975 0l3.553-3.554a3.518 3.518 0 0 0-4.974-4.975L10.3 3.7",
  ]),
  email: createSvg([
    "m10.036 8.278 9.258-7.79A1.979 1.979 0 0 0 18 0H2A1.987 1.987 0 0 0 .641.541l9.395 7.737Z",
    "M11.241 9.817c-.36.275-.801.425-1.255.427-.428 0-.845-.138-1.187-.395L0 2.6V14a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V2.5l-8.759 7.317Z",
  ]),
  globe: createSvg([
    "M6.487 1.746c0 4.192 3.592 1.66 4.592 5.754 0 .828 1 1.5 2 1.5s2-.672 2-1.5a1.5 1.5 0 0 1 1.5-1.5h1.5m-16.02.471c4.02 2.248 1.776 4.216 4.878 5.645C10.18 13.61 9 19 9 19m9.366-6h-2.287a3 3 0 0 0-3 3v2m6-8a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  ]),
};

const experience = ({
  company,
  position,
  location,
  from,
  to,
  description,
  links,
  feedback,
}: Experience) => (
  <View wrap={false} style={tw("w-full flex flex-col")}>
    <View
      style={tw(
        "w-full flex flex-col mt-2 border-2 border-slate-50 p-4 rounded-2xl"
      )}
    >
      <View style={tw("flex flex-row flex-wrap gap-1")}>
        <Text style={tw("text-amber-500")}>{position}</Text>
        <Text>at</Text>
        <Text style={tw("text-blue-500")}>
          {company}, {location}
        </Text>
        <Text>from</Text>
        <Text style={tw("text-lime-500")}>{from.toDateString()}</Text>
        <Text>to</Text>
        <Text style={tw("text-lime-500")}>{to > new Date() ? "present" : to.toDateString()}</Text>
      </View>
      {links && (
        <View style={tw("flex flex-row flex-wrap w-full gap-2 justify-center")}>
          {links.map((l) => (
            <View key={l.href} style={tw("flex flex-row gap-2 text-sm")}>
              {link(l)}
            </View>
          ))}
        </View>
      )}
      <View style={tw("m-4")}>
        <Text style={tw("text-neutral-400")}>{description}</Text>
      </View>
      {feedback && (
        <Text style={tw("w-full m-auto mb-1")}>
          <Text style={tw("text-amber-500")}>let </Text>
          <Text>clientFeedback</Text>
          <Text style={tw("text-amber-500")}> = </Text>
          <Text style={tw("text-green-500")}>"{feedback}"</Text>
          <Text>;</Text>
        </Text>
      )}
    </View>
  </View>
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
}: Education) => (
  <View style={tw("w-full flex flex-col")}>
    <View
      style={tw(
        "w-full flex flex-col mt-2 border-2 border-slate-50 p-4 rounded-2xl"
      )}
    >
      <View style={tw("flex flex-row flex-wrap gap-1")}>
        <Text style={tw("text-amber-500")}>
          Studied {degree} of {field}
        </Text>
        <Text>at</Text>
        <Text style={tw("text-blue-500")}>
          {institution}, {location}
        </Text>
        <Text>from</Text>
        <Text style={tw("text-lime-500")}>{from.toDateString()}</Text>
        <Text>to</Text>
        <Text style={tw("text-lime-500")}>{to.toDateString()}</Text>
      </View>
      {links && (
        <View style={tw("flex flex-row flex-wrap w-full gap-2 justify-center")}>
          {links.map((l) => (
            <View key={l.href} style={tw("flex flex-row gap-2")}>
              {link(l)}
            </View>
          ))}
        </View>
      )}
      <View style={tw("m-4")}>
        <Text style={tw("text-neutral-400")}>{summary}</Text>
      </View>
    </View>
  </View>
);

const certificate = ({
  name,
  issuer,
  description,
  date,
  links,
}: Certificate) => (
  <View style={tw("w-full flex flex-col")}>
    <View
      style={tw(
        "w-full flex flex-col mt-2 border-2 border-slate-50 p-4 rounded-2xl"
      )}
    >
      <View style={tw("flex flex-row flex-wrap gap-1")}>
        <Text style={tw("text-amber-500")}>{name}</Text>
        <Text>from</Text>
        <Text style={tw("text-blue-500")}>{issuer}</Text>
        <Text>on</Text>
        <Text style={tw("text-lime-500")}>{date.toDateString()}</Text>
      </View>
      {links && (
        <View style={tw("flex flex-row flex-wrap w-full gap-2 justify-center")}>
          {links.map((l) => (
            <View key={l.href} style={tw("flex flex-row gap-2")}>
              {link(l)}
            </View>
          ))}
        </View>
      )}
      <View style={tw("m-4")}>
        <Text style={tw("text-neutral-400")}>{description}</Text>
      </View>
    </View>
  </View>
);

const link = ({ text, href, icon }: A) => (
  <Link src={href} style={tw("no-underline text-slate-50 flex flex-row gap-1")}>
    {icon || svg.link}
    <Text>{text}</Text>
  </Link>
);
const Links = () => (
  <View style={tw("flex flex-row gap-4 w-full text-sm justify-center p-4")}>
    {link({
      text: "GitHub",
      href: "https://github.com/ivandimitrov8080",
      icon: svg.github as any,
    })}
    {link({
      text: "Upwork",
      href: "https://www.upwork.com/freelancers/idimitrov",
      icon: svg.link as any,
    })}
    {link({
      text: "ivan@idimitrov.dev",
      href: "mailto:ivan@idimitrov.dev",
      icon: svg.email as any,
    })}
    {link({
      text: "idimitrov.dev",
      href: "https://www.idimitrov.dev",
      icon: svg.globe as any,
    })}
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
