import Header from './components/Header'
import Hero from './components/Hero'
import Promise from './components/Promise'
import Signature from './components/Signature'
import Combos from './components/Combos'
import Menu from './components/Menu'
import Visit from './components/Visit'
import Footer from './components/Footer'

export default function App() {
  return (
    <>
      <Header />
      <main>
        <Hero />
        <Promise />
        <Signature />
        <Combos />
        <Menu />
        <Visit />
      </main>
      <Footer />
    </>
  )
}
