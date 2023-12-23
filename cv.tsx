import {
  Page,
  Text,
  View,
  Document,
} from "@react-pdf/renderer";
import ReactPDF from "@react-pdf/renderer";
import fs from "fs";
import { svg, tw } from "./theme/lib";
import SvgLink from "./theme/link";
import Experience, { Exp } from "./theme/experience";
import Education, { Edu } from "./theme/education";
import Certificate, { Cert } from "./theme/certificate";


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

const divider = (
  <View style={tw("w-full mt-4")}>
    <View style={tw("w-full border-slate-50 border-b-[.2px]")}></View>
  </View>
);

const exp: Exp[] = []
const edu: Edu[] = []
const cert: Cert[] = []

const Cv = () => (
  <Document
    title="CV"
    author="Ivan Kirilov Dimitrov"
    subject="My professional resume"
    creator="Ivan Dimitrov with react-pdf"
    producer="Ivan Dimitrov with react-pdf"
    keywords="Ivan Dimitrov Software Developer"
  >
    <Page size="A4" style={tw("w-full h-full text-slate-50 bg-gray-900 flex flex-col p-12 text-base")}>
      <Intro />
      <View style={tw("my-auto")}>
        {divider}
        <Text style={tw("text-2xl mt-2 text-violet-500")}>Experience</Text>
        {exp.map((e, i) => (
          <Experience key={i} to={e.to} from={e.from} links={e.links} company={e.company} location={e.location} position={e.position} feedback={e.feedback} description={e.description} />
        ))}
        {divider}
        <Text style={tw("text-2xl mt-2 text-violet-500")}>Education</Text>
        {edu.map((e, i) => (
          <Education key={i} to={e.to} from={e.from} links={e.links} institution={e.institution} location={e.location} field={e.field} degree={e.degree} summary={e.summary} />
        ))}
        {divider}
        <Text style={tw("text-2xl mt-2 text-violet-500")}>Certificates</Text>
        {cert.map((c, i) => (
          <Certificate key={i} date={c.date} links={c.links} name={c.name} description={c.description} issuer={c.issuer} />
        ))}
      </View>
    </Page>
  </Document >
);

type CV = {
  experience?: Exp[],
  education?: Edu[],
  certificates?: Cert[]
}
const parseData = () => {
  const data = fs.readFileSync("./cv.json", { encoding: "utf8" })
  const json: CV = JSON.parse(data)
  json.experience?.map(e => ({
    ...e,
    from: new Date(e.from),
    to: new Date(e.to)
  }))
    .forEach(e => exp.push(e))
  json.education?.map(e => ({
    ...e,
    from: new Date(e.from),
    to: new Date(e.to)
  }))
    .forEach(e => edu.push(e))
  json.certificates?.map(c => ({
    ...c,
    date: new Date(c.date)
  }))
    .forEach(c => cert.push(c))
}

const outDir = process.env.out || "./";
const pname = process.env.pname || "cv";

if (!fs.existsSync(outDir)) {
  fs.mkdirSync(outDir, { recursive: true });
}

parseData()

ReactPDF.render(<Cv />, `${outDir}/${pname}.pdf`);
