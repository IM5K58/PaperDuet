import { createRoot } from 'react-dom/client';
import './fonts';
import './style.css';
import { App } from './reader';
import { exported } from './export-api';
createRoot(document.getElementById('root')!).render(<App docId={exported.document.id} offline/>);
