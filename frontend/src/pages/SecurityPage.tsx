import { SECURITY_CATEGORIES } from "../labels";
import { AnalysisPage } from "./ScansPage";

export function SecurityPage() {
  return <AnalysisPage title="安全風險" categories={SECURITY_CATEGORIES} />;
}
