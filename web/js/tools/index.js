import { aiReview } from "./ai-review.js";
import { archiveExport } from "./archive-export.js";
import { compression } from "./compression.js";
import { googleExport } from "./google-export.js";
import { ocr } from "./ocr.js";

export const tools = [googleExport, archiveExport, ocr, aiReview, compression];
