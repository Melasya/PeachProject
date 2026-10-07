import { PageHeader } from "@/components/page-header";

export const metadata = { title: "Privacy Policy | Peach" };

const sections = [
  {
    title: "Signing in",
    body: "Peach uses Google Sign-In for authentication, through Amazon Cognito. You can also create an account with an email address and a password.",
  },
  {
    title: "What we receive",
    body: "When you sign in with Google, Peach may receive basic profile information provided by Google, including your email address.",
  },
  {
    title: "How it is used",
    body: "This information is used only to authenticate you and to display your account information in Peach, such as the email address shown in the header.",
  },
  {
    title: "Sharing",
    body: "Peach does not sell your personal information.",
  },
  {
    title: "Contact",
    body: "Peach is a student project. Questions about this policy can be raised on the project's GitHub repository, github.com/Melasya/PeachProject.",
  },
];

export default function PrivacyPage() {
  return (
    <div className="grid gap-10">
      <PageHeader
        icon="🔒"
        title="Privacy Policy"
        description="How Peach handles the information it receives when you sign in."
      />

      <div className="grid max-w-prose gap-8">
        {sections.map((section) => (
          <section key={section.title} className="grid gap-1.5">
            <h2 className="font-heading text-base font-semibold">
              {section.title}
            </h2>
            <p className="text-sm text-muted-foreground">{section.body}</p>
          </section>
        ))}
      </div>
    </div>
  );
}
