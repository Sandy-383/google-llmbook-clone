import { Suspense } from "react";
import { FullPageMessage } from "@/components/app-header";
import { NotebookView } from "@/components/notebook-view";

export default function NotebookPage() {
  return (
    <Suspense fallback={<FullPageMessage>Loading…</FullPageMessage>}>
      <NotebookView />
    </Suspense>
  );
}
