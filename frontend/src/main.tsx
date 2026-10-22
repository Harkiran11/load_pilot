import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import App from './App'
import './index.css'

// The document element is the scroll container; reserve its scrollbar gutter so
// locking body scroll while a modal is open cannot reflow the page. The class is
// a Tailwind utility because daisyUI sets `scrollbar-gutter` on `:root` from
// `@layer base`, and a layered rule beats an unlayered one.
document.documentElement.classList.add('page-gutter')

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
