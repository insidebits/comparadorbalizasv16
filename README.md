# Comparador de Balizas V16 DGT

Comparativa de luces de emergencia V16 homologadas por la DGT. Datos actualizados con precios, especificaciones técnicas y enlaces de compra en Amazon.

## Estructura

```
comparadorv16/
├── index.html    ← aplicación completa (zero-deps)
└── CNAME         ← dominio personalizado (opcional)
```

## Cómo actualizar los datos

Editar el array `BEACONS` dentro del `<script>` en `index.html`. Los campos por producto son:

| Campo | Descripción |
|-------|-------------|
| `modelo` | Nombre del modelo |
| `marca` | Fabricante |
| `link` | Enlace de afiliado Amazon |
| `precio` | Precio en € (número) |
| `tecnologia` | NB-IoT, LTE Cat NB2... |
| `duracionBateria` | Autonomía |
| `tipoPila` | 9V, AA... |
| `operador` | Vodafone, Movistar, Orange |
| `homologacion` | Código completo |
| `homologacionTipo` | LCOE o IDIADA |
| `rating` | Valoración (1-5) |
| `reviews` | Nº de reseñas |
| `imagen` | URL de imagen del producto |

## Despliegue en GitHub Pages

1. Sube este repo a GitHub
2. Settings → Pages → Source: `main` branch, root folder
3. Si tienes dominio propio, añádelo en el CNAME y configura el DNS

## Disclaimer

En calidad de Afiliado de Amazon, obtengo ingresos por las compras adscritas que cumplen los requisitos aplicables.
