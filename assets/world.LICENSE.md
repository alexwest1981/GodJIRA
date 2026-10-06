# world.svg

En varldskarta i equirectangular projektion, byggd ur Natural Earth 110m (land) via
npm-paketet `world-atlas` 2.x. Naturdata: Natural Earth, public domain. Paketets kod: ISC.

TopoJSON-arcarna ar utvikta till en enda SVG-path i gradskalan 0..360 x 0..180, sa att en
punkt satts med lon/lat rakt av: x = lon + 180, y = 90 - lat. Rundat till en decimal.
