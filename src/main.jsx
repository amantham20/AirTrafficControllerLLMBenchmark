import React from 'react';
import { createRoot } from 'react-dom/client';
import ATCBenchmark from './atc_benchmark.jsx';
import './index.css';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ATCBenchmark />
  </React.StrictMode>,
);
