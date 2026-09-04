import { BrandLogo } from "@/app/components/brand-logo";
import { PageHeader } from "@/app/components/page-header";
import { getBrandLogo } from "@/app/lib/api";

export default async function LogosPage() {
  const logo = await getBrandLogo();

  return (
    <main className="page-shell page-grid">
      <PageHeader
        description="One logo, shown in the centre of every transition between clips. Clip labels are text only."
        eyebrow="Branding"
        title="Brand Logo"
      />

      <BrandLogo logo={logo} />
    </main>
  );
}
