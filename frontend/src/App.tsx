import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Overview from './pages/Overview'
import Devices from './pages/Devices'
import Telemetry from './pages/Telemetry'
import Anomalies from './pages/Anomalies'
import Alerts from './pages/Alerts'
import Model from './pages/Model'
import Simulation from './pages/Simulation'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="/devices" element={<Devices />} />
        <Route path="/telemetry" element={<Telemetry />} />
        <Route path="/anomalies" element={<Anomalies />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route path="/model" element={<Model />} />
        <Route path="/simulation" element={<Simulation />} />
      </Route>
    </Routes>
  )
}
