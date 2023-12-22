import {
  Text,
  Link
} from "@react-pdf/renderer";
import { svg, tw } from "./lib";

export default function SvgLink({ text, href, icon }: A) {
  return <Link src={href} style={tw("no-underline text-slate-50 flex flex-row gap-1")}>
    {icon || svg.link}
    <Text>{text}</Text>
  </Link>
};
