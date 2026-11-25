/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './static/js/**/*.js',
    './app.py',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg:      'rgb(var(--bg) / <alpha-value>)',
        surface: 'rgb(var(--surface) / <alpha-value>)',
        elev:    'rgb(var(--elev) / <alpha-value>)',
        border:  'rgb(var(--border) / <alpha-value>)',
        hover:   'rgb(var(--hover) / <alpha-value>)',
        ink:     'rgb(var(--ink) / <alpha-value>)',
        muted:   'rgb(var(--muted) / <alpha-value>)',
        subtle:  'rgb(var(--subtle) / <alpha-value>)',
        accent:  'rgb(var(--accent) / <alpha-value>)',
        'accent-ink': 'rgb(var(--accent-ink) / <alpha-value>)',
        gain:    'rgb(var(--gain) / <alpha-value>)',
        loss:    'rgb(var(--loss) / <alpha-value>)',
        warn:    'rgb(var(--warn) / <alpha-value>)',
        info:    'rgb(var(--info) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['"Geist Variable"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"Geist Mono Variable"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        '2xs':   ['0.6875rem',  { lineHeight: '1rem' }],
        xs:      ['0.75rem',    { lineHeight: '1rem' }],
        sm:      ['0.8125rem',  { lineHeight: '1.25rem' }],
        base:    ['0.875rem',   { lineHeight: '1.375rem' }],
        md:      ['0.9375rem',  { lineHeight: '1.5rem' }],
        lg:      ['1rem',       { lineHeight: '1.5rem' }],
        xl:      ['1.125rem',   { lineHeight: '1.75rem' }],
        '2xl':   ['1.375rem',   { lineHeight: '1.75rem', letterSpacing: '-0.015em' }],
        '3xl':   ['1.75rem',    { lineHeight: '2rem',    letterSpacing: '-0.02em' }],
        '4xl':   ['2.25rem',    { lineHeight: '2.5rem',  letterSpacing: '-0.025em' }],
        '5xl':   ['3rem',       { lineHeight: '1',       letterSpacing: '-0.03em' }],
      },
      borderRadius: {
        lg:  '0.5rem',
        md:  '0.375rem',
        sm:  '0.25rem',
      },
      boxShadow: {
        focus: '0 0 0 2px rgb(var(--bg)), 0 0 0 4px rgb(var(--accent) / 0.5)',
        card:  '0 1px 2px 0 rgb(0 0 0 / 0.04)',
      },
      keyframes: {
        'fade-in':  { '0%': { opacity: '0' }, '100%': { opacity: '1' } },
        'slide-up': { '0%': { opacity: '0', transform: 'translateY(4px)' }, '100%': { opacity: '1', transform: 'translateY(0)' } },
        'scale-in': { '0%': { opacity: '0', transform: 'scale(0.96)' },      '100%': { opacity: '1', transform: 'scale(1)' } },
      },
      animation: {
        'fade-in':  'fade-in 180ms ease-out',
        'slide-up': 'slide-up 200ms cubic-bezier(0.16, 1, 0.3, 1)',
        'scale-in': 'scale-in 160ms cubic-bezier(0.16, 1, 0.3, 1)',
      },
    },
  },
  plugins: [],
}
