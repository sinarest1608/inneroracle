// Verse ids ("BG-2.47") -> book key / display name. Shared by the verse card
// and the inbox lists.

export const VERSE_ID = /^(BG|AG)-\d{1,2}\.\d{1,3}(-\d{1,3})?$/;
export const BOOKS = { BG: ['bhagavad_gita', 'Bhagavad Gita'], AG: ['ashtavakra_gita', 'Ashtavakra Gita'] };

export function refText(id){
  const b = BOOKS[String(id).slice(0, 2)];
  return b ? `${b[1]} ${String(id).slice(3)}` : String(id);
}
