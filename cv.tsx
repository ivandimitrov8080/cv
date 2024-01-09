import { Page, Text, View, Document } from "@react-pdf/renderer";
import ReactPDF from "@react-pdf/renderer";
import fs from "fs";
import { svg, tw } from "./theme/lib";
import SvgLink from "./theme/link";
import Experience, { Exp } from "./theme/experience";
import Education, { Edu } from "./theme/education";
import Certificate, { Cert } from "./theme/certificate";

const divider = (
  <View style={tw("w-full mt-4")}>
    <View style={tw("w-full border-slate-50 border-b-[.2px]")}></View>
  </View>
);

type CV = {
  name: string;
  description: string;
  title: string;
  email: string;
  github?: string;
  website?: string;
  upwork?: string;
  phone?: string;
  experience?: Exp[];
  education?: Edu[];
  certificates?: Cert[];
};

let data: CV = {} as CV;

const Cv = () => (
  <Document
    title="CV"
    author={data.name}
    subject="My professional resume"
    creator={`${data.name} with react-pdf`}
    producer={`${data.name} with react-pdf`}
    keywords={`${data.name} ${data.title}`}
  >
    <Page
      size="A4"
      style={tw(
        "w-full h-full text-slate-50 bg-gray-900 flex flex-col p-12 text-base"
      )}
    >
      <View style={tw("text-center border-2 border-slate-50 rounded-full")}>
        <Text style={tw("text-5xl")}>{data.name}</Text>
        <Text>{data.title}</Text>
        <View
          style={tw("flex flex-row gap-4 w-full text-sm justify-center p-4")}
        >
          {data.github && (
            <SvgLink
              text="GitHub"
              href={`https://github.com/${data.github}`}
              icon={svg.github as any}
            />
          )}
          {data.upwork && (
            <SvgLink
              text="Upwork"
              href={`https://www.upwork.com/freelancers/${data.upwork}`}
              icon={svg.link as any}
            />
          )}
          <SvgLink
            text={data.email}
            href={`mailto:${data.email}`}
            icon={svg.email as any}
          />
          {data.phone && (
            <SvgLink
              text={data.phone}
              href={`tel:${data.phone}`}
              icon={svg.phone as any}
            />
          )}
          {data.website && (
            <SvgLink
              text={data.website}
              href={`https://${data.website}`}
              icon={svg.globe as any}
            />
          )}
        </View>
      </View>
      <View style={tw("my-auto")}>
        {divider}
        <Text style={tw("text-2xl mt-2 text-violet-500")}>Experience</Text>
        {data.experience?.map((e, i) => (
          <Experience
            key={i}
            to={e.to}
            from={e.from}
            links={e.links}
            company={e.company}
            location={e.location}
            position={e.position}
            feedback={e.feedback}
            description={e.description}
          />
        ))}
        {data.education && (
          <>
            {divider}
            <Text style={tw("text-2xl mt-2 text-violet-500")}>Education</Text>
            {data.education.map((e, i) => (
              <Education
                key={i}
                to={e.to}
                from={e.from}
                links={e.links}
                institution={e.institution}
                location={e.location}
                field={e.field}
                degree={e.degree}
                summary={e.summary}
              />
            ))}
          </>
        )}
        {data.certificates && (
          <>
            {divider}
            <Text style={tw("text-2xl mt-2 text-violet-500")}>Certificates</Text>
            {data.certificates?.map((c, i) => (
              <Certificate
                key={i}
                date={c.date}
                links={c.links}
                name={c.name}
                description={c.description}
                issuer={c.issuer}
              />
            ))}
          </>
        )}
      </View>
    </Page>
  </Document>
);

const parseData = () => {
  const d = fs.readFileSync("./cv.json", { encoding: "utf8" });
  const json: CV = JSON.parse(d);
  json.experience = json.experience?.map((e) => ({
    ...e,
    from: new Date(e.from),
    to: new Date(e.to),
  }));
  json.education = json.education?.map((e) => ({
    ...e,
    from: new Date(e.from),
    to: new Date(e.to),
  }));
  json.certificates = json.certificates?.map((c) => ({
    ...c,
    date: new Date(c.date),
  }));
  data = json;
};

const outDir = process.env.out || "./";
const pname = process.env.pname || "cv";

if (!fs.existsSync(outDir)) {
  fs.mkdirSync(outDir, { recursive: true });
}

parseData();

ReactPDF.render(<Cv />, `${outDir}/${pname}.pdf`);
