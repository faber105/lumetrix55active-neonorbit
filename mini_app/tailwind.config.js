export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        terminal: {
          bg: '#0D0D0D',
          card: '#1A1A1A',
          hover: '#222222',
          border: '#2A2A2A',
          text: '#FFFFFF',
          muted: '#8A8A8A',
          green: '#00FFA3',
          red: '#FF4D4D'
        }
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
        mono: ['Roboto Mono', 'monospace']
      },
      boxShadow: {
        pulse: '0 0 28px rgba(0, 255, 163, 0.2)'
      }
    }
  },
  plugins: []
};

