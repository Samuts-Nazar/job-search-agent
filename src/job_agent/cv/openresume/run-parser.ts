// CLI entry point invoked by job_agent.cv.openresume_check (Python) via
// `npx tsx run-parser.ts <pdf-path>`. Prints the parsed resume as JSON to
// stdout, or a JSON error object with a non-zero exit code on failure.
import { parseResumeFromPdf } from "lib/parse-resume-from-pdf/index";

const pdfPath = process.argv[2];
if (!pdfPath) {
  console.error(JSON.stringify({ error: "Usage: run-parser.ts <pdf-path>" }));
  process.exit(1);
}

try {
  const resume = await parseResumeFromPdf(pdfPath);
  console.log(JSON.stringify(resume, null, 2));
} catch (err) {
  console.error(JSON.stringify({ error: String(err) }));
  process.exit(1);
}
