import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "data/source/政治学与公共管理期刊目录_已补官网.xlsx";
const outputPath = "data/journals.json";

const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const sheet = workbook.worksheets.getItem("总表");
const values = sheet.getRange("A1:K193").values;
const rows = values.slice(1).filter((row) => row.some((cell) => cell !== null && cell !== ""));

const journals = rows.map((row) => ({
  id: Number(row[0]),
  field: String(row[1] ?? ""),
  database: String(row[2] ?? ""),
  category: String(row[3] ?? ""),
  title: String(row[4] ?? ""),
  language: String(row[5] ?? ""),
  source_note: String(row[6] ?? ""),
  homepage_url: String(row[7] ?? ""),
  platform: String(row[8] ?? ""),
  verification_status: String(row[9] ?? ""),
  homepage_note: String(row[10] ?? ""),
  feed_url: "",
  enabled: true,
}));

await fs.writeFile(outputPath, `${JSON.stringify(journals, null, 2)}\n`, "utf8");
console.log(JSON.stringify({ outputPath, count: journals.length }, null, 2));
