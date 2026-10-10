import { useEffect, useState } from "react";

const API_URL = "http://localhost:8010";

function App() {
  const [vehicles, setVehicles] = useState([]);
  const [drivers, setDrivers] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    const load = (path, setter) =>
      fetch(`${API_URL}${path}`)
        .then((res) => {
          if (!res.ok) throw new Error(`${path} returned ${res.status}`);
          return res.json();
        })
        .then(setter)
        .catch((err) => setError(err.message));
    load("/vehicles", setVehicles);
    load("/drivers", setDrivers);
  }, []);

  return (
    <div>
      <h1>Dispatch Console</h1>
      {error && <p>Could not load data: {error}</p>}
      <h2>Vehicles</h2>
      {vehicles.length === 0 && <p>No vehicles yet.</p>}
      <ul>
        {vehicles.map((v) => (
          <li key={v.id}>
            {v.plate} ({v.vehicle_type}, {v.capacity_kg} kg)
          </li>
        ))}
      </ul>
      <h2>Drivers</h2>
      {drivers.length === 0 && <p>No drivers yet.</p>}
      <ul>
        {drivers.map((d) => (
          <li key={d.id}>
            {d.full_name} ({d.licence_number})
          </li>
        ))}
      </ul>
    </div>
  );
}

export default App;