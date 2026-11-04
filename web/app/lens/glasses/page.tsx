import type { Metadata } from 'next';
import { GlassesApp } from './glasses-app';

export const metadata: Metadata = {
  title: 'Den on your glasses',
  description: 'Den on Meta Ray-Ban Display glasses: ask by voice, see your contracts and your crew’s replies.',
  robots: { index: false, follow: false },
};

export default function GlassesPage() {
  return <GlassesApp />;
}
