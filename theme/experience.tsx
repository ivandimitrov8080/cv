import {
  Text,
  View,
} from "@react-pdf/renderer";
import { tw } from "./lib";
import SvgLink from "./link";

type Props = {
  company: string;
  position: string;
  location: string;
  from: Date;
  to: Date;
  description: string;
  links?: A[];
  feedback?: string;
}

export default function Experience({ company, position, location, from, to, description, links, feedback }: Props) {
  return (<View wrap={false} style={tw("w-full flex flex-col")}>
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
              <SvgLink text={l.text} href={l.href} icon={l.icon} />
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
  )
}
