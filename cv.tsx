import { Page, Text, View, Document, Link } from "@react-pdf/renderer";
import ReactPDF from "@react-pdf/renderer";
import fs from "fs";
import { createTw } from "react-pdf-tailwind";

type Experience = {
  company: string
  position: string
  location: string
  from: Date
  to: Date
  description: string
  technologies: string[]
}

const tw = createTw({
});

const link = "no-underline text-red-50"
const section = "w-full flex flex-col m-4"

const divider =
  <View style={tw("w-full mt-4")}>
    <View style={tw("w-full border-slate-50 border-b-[.02px]")}></View>
  </View>


const github =
  <Link src="https://github.com/ivandimitrov8080" style={tw(link)}>github/ivandimitrov8080</Link>
const upwork =
  <Link src="https://www.upwork.com/freelancers/idimitrov" style={tw(link)}>upwork/freelancers/idimitrov</Link>
const resume =
  <Link src="https://www.idimitrov.dev" style={tw(link)}>idimitrov.dev</Link>
const email =
  <Link src="mailto:ivan@idimitrov.dev" style={tw(link)}>ivan@idimitrov.dev</Link>

const tech = {
  android: [
    "Android",
    "Android Studio",
  ],
  java: [
    "Java",
    "JPA",
    "Hibernate",
    "Spring Framework",
    "Spring Boot",
    "Lombok",
    "Spring MVC",
    "Thymeleaf",
    "JSP",
    "JSTL",
    "XML",
    "Spring Security",
    "OAuth2",
    "H2",
    "Spring Boot Actuator",
  ],
  web: [
    "JavaScript",
    "TypeScript",
    "React",
    "HTML",
    "CSS"
  ],
  api: [
    "REST",
    "SOAP",
  ],
  db: [
    "MySQL",
    "PostgreSQL"
  ],
  linux: [
    "Linux",
    "Bash",
    "coreutils",
    "Ubuntu",
    "CentOS",
    "RHEL",
    "SSH",
    "iptables",
    "systemd",
    "vim",
    "Monit",
    "CLI",
  ],
  hybris: [
    "SAP hybris",
    "ZK Framework",
  ],
  payment: [
    "PayPal",
    "Adyen",
    "V12",
    "Wells Fargo Open Banking APIs",
  ]
}

const techKeys = [...Object.keys(tech)] as const

const cnetTech: string[] = techKeys
  .filter(e => !e.includes("hybris"))
  .filter(e => !e.includes("payment"))
  // @ts-ignore
  .map(e => tech[e]).flat()
const raTech: string[] = techKeys
  // @ts-ignore
  .map(e => tech[e]).flat()


const experience = ({
  company,
  position,
  location,
  from,
  to,
  description,
  technologies
}: Experience) => (
  <View style={tw("w-full flex flex-col mt-2 border-2 border-slate-50 p-4 rounded-2xl")}>
    <View style={tw("flex flex-row flex-wrap gap-1")}>
      <Text>{position}</Text>
      <Text>at</Text>
      <Text>{company}, {location}</Text>
      <Text>from</Text>
      <Text>{from.toDateString()}</Text>
      <Text>to</Text>
      <Text>{to.toDateString()}</Text>
    </View>
    <View style={tw("m-4")}>
      <Text>{description}</Text>
    </View>
    <View style={tw("flex flex-row flex-wrap")}>
      {technologies.map(t => (
        <View key={t} style={tw("flex flex-row items-center")}>
          <View style={tw("border-2 border-teal-200 h-1 mx-1 mb-1 rounded-full")}></View>
          <Text style={tw("text-sm")}>{t}</Text>
        </View>
      ))}
    </View>
  </View>
)

const Links = () => (
  <View style={tw("flex flex-row gap-4 w-full text-sm justify-center p-4")}>
    {github}
    {upwork}
    {email}
    {resume}
  </View>
)

const Experience = () => (
  <View style={tw(section)}>
    <Text style={tw("text-3xl")}>Experience</Text>
    {experience({
      company: "Central Net",
      position: "Software Developer",
      location: "Blagoevgrad, Bulgaria",
      from: new Date("May 2016"),
      to: new Date("May 2020"),
      description: "Developed a full-stack web app helping students book exams, browse resources, see events, news and more.",
      technologies: cnetTech
    })}
    {experience({
      company: "RA Creative",
      position: "Software Developer",
      location: "Nottingham, UK",
      from: new Date("Dec 2020"),
      to: new Date("20 Jan 2023"),
      description: "Worked on seven international eCommerce web apps serving customers in the US and Europe.",
      technologies: raTech
    })}
  </View>
)

const Intro = () => (
  <View style={tw("text-center border-2 border-slate-50 rounded-full")}>
    <Text style={tw("text-5xl")}>Ivan K. Dimitrov</Text>
    <Text style={tw("text-sm")}>Software Developer</Text>
    <Links />
  </View>
)

const CV = () => (
  <Document>
    <Page
      size="A4"
      style={tw("w-full h-full text-slate-50 bg-slate-950 flex flex-col p-12 text-base")}>
      <Intro />
      {divider}
      <Experience />
    </Page>
  </Document>
);

const outDir = process.env.out || "./"
const pname = process.env.pname || "cv"

if (!fs.existsSync(outDir)) {
  fs.mkdirSync(outDir, { recursive: true })
}

ReactPDF.render(<CV />, `${outDir}/${pname}.pdf`);

