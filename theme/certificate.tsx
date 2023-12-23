import {
  Text,
  View,
} from "@react-pdf/renderer";
import { tw } from "./lib";
import SvgLink from "./link";

export type Cert = {
  name: string;
  issuer: string;
  description: string;
  date: Date;
  links?: A[];
};


export default function Certificate({
  name,
  issuer,
  description,
  date,
  links,
}: Cert) {
  return (
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
                <SvgLink text={l.text} href={l.href} icon={l.icon} />
              </View>
            ))}
          </View>
        )}
        <View style={tw("m-4")}>
          <Text style={tw("text-neutral-400")}>{description}</Text>
        </View>
      </View>
    </View>
  )
}
