/**
 * Remove tool-progress chatter that some models prepend to the saved report and
 * escape approximation tildes that GFM would otherwise read as strikethrough.
 */
export function cleanAgentMarkdown(markdown: string): string {
  const normalized = markdown.replace(/\r\n?/g, "\n").trim();
  const firstHeading = normalized.search(/#{1,6}\s+\S/);
  const report = firstHeading > 0 ? normalized.slice(firstHeading) : normalized;
  return report.replace(/(^|[^\\])~(?=[$€£₹]?\d)/g, "$1\\~");
}
