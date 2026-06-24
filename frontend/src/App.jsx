import Header from './components/Header'
import Hero from './components/Hero'
import Bestseller from './components/Bestseller'
import Menu from './components/Menu'
import Location from './components/Location'
import Hours from './components/Hours'
import Footer from './components/Footer'

export default function App() {
  return (
    <>
      <Header />
      <main>
        <Hero />
        <Bestseller />
        <Menu />
        <Location />
        <Hours />
      </main>
      <Footer />
    </>
  )
}
