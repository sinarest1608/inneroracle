// Starter prompts for the empty state — three picked at random each render.

const SUGGESTIONS = [
  'How do I deal with anxiety about results?', 'What is the self?',
  'How should I handle anger?', 'How do I find peace in difficult times?',
  'What does it mean to act without attachment?', 'How do I face the fear of failure?',
  'What is true freedom?', 'How should I cope with loss and grief?',
  'How do I quiet a restless mind?', 'What is my duty when I feel torn?',
  'How do I let go of control?', 'How do I overcome craving and desire?',
  'How can I be content with what I have?', 'What is the path to lasting happiness?',
  'How do I stay steady amid praise and blame?',
];

export function pick3(){
  const a = SUGGESTIONS.slice();
  for (let i=a.length-1;i>0;i--){ const j=(Math.random()*(i+1))|0; [a[i],a[j]]=[a[j],a[i]]; }
  return a.slice(0,3);
}
