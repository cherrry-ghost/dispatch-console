import { useState } from "react";

const API_URL = "http://localhost:8010";

// Small login bar. The token lands in localStorage; main.jsx attaches it
// to every API call. Without a login, every POST will be refused with 401.
function LoginBar() {
  const [token, setToken] = useState(() => localStorage.getItem("token"));
  const [username, setUsername] = useState(() =>
    localStorage.getItem("username"));
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");

  const login = async (event) => {
    event.preventDefault();
    setMessage("");
    try {
      const res = await fetch(`${API_URL}/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: user, password }),
      });
      const body = await res.json();
      if (!res.ok) {
        setMessage(body.detail || "Login failed");
        return;
      }
      localStorage.setItem("token", body.token);
      localStorage.setItem("username", body.username);
      setToken(body.token);
      setUsername(body.username);
      setPassword("");
      // reload so every panel refetches with the token in place
      window.location.reload();
    } catch (err) {
      setMessage("Could not reach the server: " + err.message);
    }
  };

  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("username");
    setToken(null);
    window.location.reload();
  };

  if (token) {
    return (
      <p style={{ margin: "4px 0" }}>
        Logged in as <b>{username}</b>{" "}
        <button onClick={logout} style={{ marginLeft: 8 }}>Log out</button>
      </p>
    );
  }
  return (
    <form onSubmit={login} style={{ margin: "8px 0", display: "flex",
                                     gap: 6, alignItems: "center" }}>
      <b>Sign in</b>
      <input value={user} onChange={(e) => setUser(e.target.value)}
             placeholder="username" size={10} />
      <input value={password} onChange={(e) => setPassword(e.target.value)}
             placeholder="password" type="password" size={10} />
      <button type="submit">Log in</button>
      {message && <span style={{ color: "#b91c1c" }}>{message}</span>}
    </form>
  );
}

export default LoginBar;
