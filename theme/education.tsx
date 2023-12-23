import { Text, View } from "@react-pdf/renderer";
import { tw } from "./lib";
import SvgLink from "./link";

export type Edu = {
  institution: string;
  location: string;
  degree: string;
  field: string;
  from: Date;
  to: Date;
  summary: string;
  links?: A[];
};

export default function Education({
  institution,
  location,
  degree,
  field,
  from,
  to,
  summary,
  links,
}: Edu) {
  return (
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
          <View
            style={tw("flex flex-row flex-wrap w-full gap-2 justify-center")}
          >
            {links.map((l) => (
              <View key={l.href} style={tw("flex flex-row gap-2")}>
                <SvgLink text={l.text} href={l.href} icon={l.icon} />
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
}
