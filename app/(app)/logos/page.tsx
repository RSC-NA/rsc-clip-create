import { LogoLibrary } from "@/app/components/logo-library";
import { PageHeader } from "@/app/components/page-header";
import { SummaryCard } from "@/app/components/summary-card";
import { getLogos } from "@/app/lib/api";

export default async function LogosPage() {
  const logos = await getLogos();

  return (
    <main className="page-shell page-grid">
      <PageHeader
        description="Logos are matched to clips by the team name in the filename. Upload one per team and every clip for that team carries it in the bottom-left label."
        eyebrow="Branding"
        title="Team Logo Library"
      />

      <section className="stats-grid">
        <SummaryCard
          detail="Teams with a logo ready to burn into their clips."
          label="Teams Covered"
          value={logos.length}
        />
        <SummaryCard
          detail="Clips named team-player-tier.ext match on the first token."
          label="Match Rule"
          value="team"
        />
      </section>

      <LogoLibrary logos={logos} />
    </main>
  );
}
