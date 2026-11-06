/** What "Where to?" sends (moved verbatim from app/index.tsx): a bare place ("Reno") becomes "Take me to Reno";
 *  a full request ("I need fuel") goes as typed. */
export const searchRequest = (q: string): string =>
  /\b(?:i|i'm|me|my|need|find|take|go|where|show|get|park|near|any|truck ?stops?|hotels?|motels?|rest ?areas?|fuel|diesel|gas|parking)\b/i.test(q) ? q : `Take me to ${q}`;
