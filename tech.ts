const tech = {
  android: ["Android", "Android Studio"],
  architectures: [
    "Microservices",
    "MVC",
    "Layered architecture",
    "DDD",
    "EDA",
    "Publish-subscribe",
    "Client-server",
    "REST",
    "Pipes and filters",
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
    "Maven",
    "Gradle",
    "Ant",
  ],
  web: ["JavaScript", "HTML", "CSS"],
  api: ["REST", "SOAP"],
  db: ["MySQL", "PostgreSQL"],
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
    "pandoc",
    "LUKS",
    "hexdump",
    "dd",
  ],
  git: ["git", "GitHub", "GitLab", "BitBucket"],
  hybris: ["SAP hybris", "ZK Framework"],
  payment: ["PayPal", "Adyen", "V12", "Wells Fargo Open Banking APIs"],
  dataIntegration: [
    "Spring Batch",
    "Data Pipeline",
    "Scriptella",
    "Easy Batch",
    "GETL",
    "Apache Camel",
    "Apache Samza",
    "Apache Flink",
    "Apache Storm",
    "Apache Spark",
    "Apache NiFi",
  ],
  python: [
    "python",
    "BeautifulSoup4",
    "requests",
    "pypandoc",
    "markdownify",
    "html2text",
    "Poetry",
  ],
  javascript: ["TypeScript", "React"],
  nextjs: [
    "NextJS 12",
    "NextJS 13",
    "NextJS 14",
    "NextAuth",
    "Prisma",
    "Vercel",
    "Vercel Postgres",
    "Formik",
    "Framer Motion",
  ],
  styles: [
    "CSS",
    "SASS",
    "TailwindCSS",
    "DaisyUI",
    "tailwind-scrollbar",
    "FontAwesome",
    "nProgress",
  ],
  general: ["Markdown", "Google", "DuckDuckGo", "PDF", "Email"],
} as const;

const techKeys = Object.keys(tech) as Array<keyof typeof tech>;

type TechKeys = keyof typeof tech;

export const skills = (skills: TechKeys[]): string[] => {
  return skills.map((s) => tech[s]).flat();
};

export const skillsInverted = (skills: TechKeys[]): string[] => {
  return techKeys
    .filter((k) => !skills.includes(k))
    .map((s) => tech[s])
    .flat();
};
