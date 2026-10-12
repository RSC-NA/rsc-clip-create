import { BrandSweep } from "@/app/components/brand-sweep";
import { PageHeader } from "@/app/components/page-header";
import { getBrandSweep, getClientConfig } from "@/app/lib/api";

export default async function SweepsPage() {
  const [sweep, config] = await Promise.all([getBrandSweep(), getClientConfig()]);

  return (
    <main className="page-shell page-grid">
      <PageHeader
        description="The stinger played over every cut between clips. One per account; leave it unset to use the bundled sweep."
        eyebrow="Branding"
        title="Brand Sweep"
      />

      <BrandSweep sweep={sweep} supportedFormats={config.supportedSweepFormats} />
    </main>
  );
}
