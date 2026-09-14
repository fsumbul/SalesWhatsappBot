import { notFound } from "next/navigation";
import WorkflowGallery from "../../../../components/workspace/workflow-gallery";
export default function Page() {
  if (process.env.NODE_ENV !== "development") notFound();
  return <WorkflowGallery />;
}
