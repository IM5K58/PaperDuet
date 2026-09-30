import { createRoot } from 'react-dom/client';
import './fonts';
import './style.css';
import { Workspace } from './library';

createRoot(document.getElementById('root')!).render(<Workspace />);
