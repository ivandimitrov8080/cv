{-# LANGUAGE DeriveGeneric #-}
{-# LANGUAGE OverloadedStrings #-}

-------------------------------------------------------------------------------
-- Config Section: fonts, colors, layouts, style options
-------------------------------------------------------------------------------
module Main where

import Control.Monad (foldM)
import Data.Aeson (FromJSON (..), Value, defaultOptions, eitherDecode, fieldLabelModifier, genericParseJSON)
import Data.Aeson.Types (Parser)
import Data.ByteString.Lazy qualified as BSL
import Data.Char (toLower)
import Data.Maybe (fromMaybe)
import Data.Text qualified as T
import GHC.Generics (Generic)
import Graphics.PDF
import Graphics.PDF.Fonts.StandardFont

-- | Strip a prefix and lowercase the following char.
stripPrefixLower :: String -> String -> String
stripPrefixLower pre s = case splitAt (length pre) s of
  (p, rest)
    | map toLower p == map toLower pre && not (null rest) -> toLower (head rest) : tail rest
    | map toLower p == map toLower pre -> ""
    | otherwise -> s

fieldMap :: [(String, String)] -> String -> String
fieldMap m k = fromMaybe k (lookup k m)

data FontConfig = FontConfig
  { fontHeader :: String,
    fontBody :: String,
    sizeHeader :: Double,
    sizeBody :: Double
  }
  deriving (Show, Generic)

instance FromJSON FontConfig

data ColorConfig = ColorConfig
  { fgColor :: String,
    bgColor :: String,
    accentColor :: String
  }
  deriving (Show, Generic)

instance FromJSON ColorConfig

data LayoutConfig = LayoutConfig
  { marginTop :: Double,
    marginBottom :: Double,
    marginLeft :: Double,
    marginRight :: Double,
    sectionSpacing :: Double
  }
  deriving (Show, Generic)

instance FromJSON LayoutConfig

data CVConfig = CVConfig
  { font :: Maybe FontConfig,
    color :: Maybe ColorConfig,
    layout :: Maybe LayoutConfig
  }
  deriving (Show, Generic)

instance FromJSON CVConfig

defaultFontConfig :: FontConfig
defaultFontConfig = FontConfig "Helvetica-Bold" "Helvetica" 18 11

defaultColorConfig :: ColorConfig
defaultColorConfig = ColorConfig "#000000" "#FFFFFF" "#505050"

defaultLayoutConfig :: LayoutConfig
defaultLayoutConfig = LayoutConfig 36 36 36 36 18

-------------------------------------------------------------------------------
-- CV Section: data types corresponding to sections in the CV
-------------------------------------------------------------------------------
data Link = Link
  { linkText :: String,
    linkHref :: String
  }
  deriving (Show, Generic)

instance FromJSON Link where
  parseJSON = genericParseJSON defaultOptions {fieldLabelModifier = stripPrefixLower "link"}

data PersonalInfo = PersonalInfo
  { piName :: String,
    piDescription :: Maybe String,
    piTitle :: Maybe String,
    piEmail :: Maybe String,
    piGithub :: Maybe String,
    piWebsite :: Maybe String,
    piUpwork :: Maybe String
  }
  deriving (Show, Generic)

instance FromJSON PersonalInfo where
  parseJSON = genericParseJSON defaultOptions {fieldLabelModifier = stripPrefixLower "pi"}

data Experience = Experience
  { expCompany :: String,
    expPosition :: Maybe String,
    expLocation :: Maybe String,
    expFrom :: Maybe String,
    expTo :: Maybe String,
    expDescription :: Maybe String,
    expLinks :: Maybe [Link],
    expFeedback :: Maybe String
  }
  deriving (Show, Generic)

instance FromJSON Experience where
  parseJSON = genericParseJSON defaultOptions {fieldLabelModifier = stripPrefixLower "exp"}

data Education = Education
  { eduInstitution :: String,
    eduLocation :: Maybe String,
    eduDegree :: Maybe String,
    eduField :: Maybe String,
    eduFrom :: Maybe String,
    eduTo :: Maybe String,
    eduSummary :: Maybe String
  }
  deriving (Show, Generic)

instance FromJSON Education where
  parseJSON = genericParseJSON defaultOptions {fieldLabelModifier = stripPrefixLower "edu"}

data Skill = Skill
  { skillCategory :: String,
    skillItems :: [String]
  }
  deriving (Show, Generic)

instance FromJSON Skill where
  parseJSON = genericParseJSON defaultOptions {fieldLabelModifier = stripPrefixLower "skill"}

data Certificate = Certificate
  { certName :: String,
    certIssuer :: Maybe String,
    certDescription :: Maybe String,
    certDate :: Maybe String,
    certLinks :: Maybe [Link]
  }
  deriving (Show, Generic)

instance FromJSON Certificate where
  parseJSON = genericParseJSON defaultOptions {fieldLabelModifier = stripPrefixLower "cert"}

data CV = CV
  { cvPersonalInfo :: PersonalInfo,
    cvExperience :: [Experience],
    cvEducation :: [Education],
    cvCertificates :: Maybe [Certificate],
    cvSkills :: Maybe [Skill],
    cvConfig :: Maybe CVConfig
  }
  deriving (Show, Generic)

instance FromJSON CV where
  parseJSON = genericParseJSON defaultOptions {fieldLabelModifier = stripPrefixLower "cv"}

-------------------------------------------------------------------------------
-- Render Section: rendering logic for transforming CV -> PDF
-------------------------------------------------------------------------------

-- | All config and fonts needed to render
--   Also carries the full CV for convenient section extraction
--   Can be extended with more environment/config fields as needed
-- | Which personal info fields to display, and their layout/order.
data PersonalInfoField
  = InfoName
  | InfoTitle
  | InfoDescription
  | InfoEmail
  | InfoGithub
  | InfoWebsite
  | InfoUpwork
  deriving (Show, Eq, Ord)

data PersonalInfoLayout = PersonalInfoLayout
  { infoFieldOrder :: [PersonalInfoField],
    infoStartY :: Cursor,
    infoLineStep :: Cursor
  }
  deriving (Show)

-- | All config and fonts needed to render
data RenderEnv = RenderEnv
  { reCV :: CV,
    reFontConfig :: FontConfig,
    reColorConfig :: ColorConfig,
    reLayoutConfig :: LayoutConfig,
    reFontHeader :: PDFFont,
    reFontBody :: PDFFont,
    reInfoLayout :: PersonalInfoLayout
  }

type Cursor = PDFFloat

startY :: PDFFloat
startY = 770

drawInfoLines :: PDFFont -> Cursor -> [String] -> Draw Cursor
drawInfoLines font y linesAll = foldM go y linesAll
  where
    go yc l = drawText (setFont font >> text font 72 yc (T.pack l)) >> return (yc - 16)

drawSection :: RenderEnv -> String -> Cursor -> Draw Cursor
drawSection env s y =
  let fontHeader = reFontHeader env
   in drawText (setFont fontHeader >> text fontHeader 72 y (T.pack s)) >> return (y - 18)

drawPersonalInfo :: RenderEnv -> Cursor -> Draw Cursor
drawPersonalInfo env _ = do
  let fontHeader = reFontHeader env
      pi = cvPersonalInfo (reCV env)
      layout = reInfoLayout env
      fields = infoFieldOrder layout
      y0 = infoStartY layout
      step = infoLineStep layout
      toText f = case f of
        InfoName -> piName pi
        InfoTitle -> maybe "" id (piTitle pi)
        InfoDescription -> maybe "" id (piDescription pi)
        InfoEmail -> maybe "" id (piEmail pi)
        InfoGithub -> if maybe "" id (piGithub pi) /= "" then "github: " ++ maybe "" id (piGithub pi) else ""
        InfoWebsite -> if maybe "" id (piWebsite pi) /= "" then "website: " ++ maybe "" id (piWebsite pi) else ""
        InfoUpwork -> if maybe "" id (piUpwork pi) /= "" then "upwork: " ++ maybe "" id (piUpwork pi) else ""
      linesAll = filter (/= "") $ map toText fields
  -- Print all info lines vertically with configured spacing
  foldM
    (\yc l -> drawText (setFont fontHeader >> text fontHeader 72 yc (T.pack l)) >> return (yc - step))
    y0
    linesAll

drawExperience :: RenderEnv -> Cursor -> Experience -> Draw Cursor
drawExperience env y e = do
  let font = reFontBody env
  drawText $ do setFont font; text font 72 y . T.pack $ expCompany e ++ maybe "" (" / " ++) (expPosition e) ++ maybe "" (" — " ++) (expLocation e)
  let y1 = y - 14
  drawText $ text font 82 y1 . T.pack $ maybe "" id (expFrom e) ++ " - " ++ maybe "" id (expTo e)
  let y2 = y1 - 14
  case expDescription e of Just desc | not (null desc) -> drawText (text font 82 y2 (T.pack desc)) >> return (y2 - 14); _ -> return y2

drawEducation :: RenderEnv -> Cursor -> Education -> Draw Cursor
drawEducation env y e = do
  let font = reFontBody env
  drawText $ do setFont font; text font 72 y . T.pack $ eduInstitution e ++ maybe "" (", " ++) (eduDegree e) ++ maybe "" (", " ++) (eduField e)
  drawText $ text font 82 (y - 14) . T.pack $ maybe "" id (eduFrom e) ++ " - " ++ maybe "" id (eduTo e)
  case eduSummary e of Just summary | not (null summary) -> drawText (text font 82 (y - 28) (T.pack summary)) >> return (y - 42); _ -> return (y - 28)

drawCertificate :: RenderEnv -> Cursor -> Certificate -> Draw Cursor
drawCertificate env y c = do
  let font = reFontBody env
  drawText $ do setFont font; text font 72 y . T.pack $ certName c ++ maybe "" ((" (" ++) . (++ ")")) (certIssuer c)
  drawText $ text font 82 (y - 14) (T.pack (maybe "" id (certDate c)))
  case certDescription c of Just desc | not (null desc) -> drawText (text font 82 (y - 28) (T.pack desc)) >> return (y - 42); _ -> return (y - 28)

drawSkill :: RenderEnv -> Cursor -> Skill -> Draw Cursor
drawSkill env y s =
  let font = reFontBody env
   in drawText (setFont font >> text font 72 y (T.pack (skillCategory s ++ ": " ++ unwords (skillItems s)))) >> return (y - 14)

renderCVPage :: RenderEnv -> Draw ()
renderCVPage env = do
  let cv = reCV env
      experience = cvExperience cv
      education = cvEducation cv
      certificates = cvCertificates cv
      skills = cvSkills cv
  y1 <- drawPersonalInfo env startY
  y2 <- drawSection env "Experience" y1
  y3 <- foldM (drawExperience env) (y2 - 20) experience
  y4 <- drawSection env "Education" (y3 - 30)
  y5 <- foldM (drawEducation env) (y4 - 20) education
  y6 <- case certificates of
    Just certs -> do
      ycert <- drawSection env "Certificates" (y5 - 30)
      foldM (drawCertificate env) (ycert - 20) certs
    Nothing -> return y5
  _y7 <- case skills of
    Just sks -> do
      yskill <- drawSection env "Skills" (y6 - 30)
      foldM (drawSkill env) (yskill - 20) sks
    Nothing -> return y6
  return ()

renderCV :: RenderEnv -> PDF ()
renderCV env = do
  page <- addPage Nothing
  drawWithPage page (renderCVPage env)

-------------------------------------------------------------------------------
-- Program Section: read JSON, parse and drive the rendering (main IO)
-------------------------------------------------------------------------------
main :: IO ()
main = do
  fontHeaderResult <- mkStdFont Times_Bold
  fontBodyResult <- mkStdFont Times_Roman
  let fontHeader = case fontHeaderResult of Right fh -> fh; Left e -> error ("Header font error: " ++ show e)
      fontBody = case fontBodyResult of Right fb -> fb; Left e -> error ("Body font error: " ++ show e)
  jsonData <- BSL.readFile "cv.json"
  let parseResult = eitherDecode jsonData :: Either String CV
  case parseResult of
    Left err -> putStrLn $ "Error parsing cv.json: " ++ err
    Right cv -> do
      putStrLn "Successfully parsed cv.json! Generating cv.pdf..."
      let cvConfig' = maybe (CVConfig Nothing Nothing Nothing) id (cvConfig cv)
          fontConfig = maybe defaultFontConfig id (font cvConfig')
          colorConfig = maybe defaultColorConfig id (color cvConfig')
          layoutConfig = maybe defaultLayoutConfig id (layout cvConfig')
          infoLayout =
            PersonalInfoLayout
              { infoFieldOrder = [InfoName, InfoTitle, InfoDescription, InfoEmail, InfoGithub, InfoWebsite, InfoUpwork],
                infoStartY = startY, -- can be customized
                infoLineStep = 16 -- can be customized
              }
          env =
            RenderEnv
              { reCV = cv,
                reFontConfig = fontConfig,
                reColorConfig = colorConfig,
                reLayoutConfig = layoutConfig,
                reFontHeader = PDFFont fontHeader (round (sizeHeader fontConfig)),
                reFontBody = PDFFont fontBody (round (sizeBody fontConfig)),
                reInfoLayout = infoLayout
              }
      runPdf "out/cv.pdf" standardDocInfo (PDFRect 0 0 595 842) (renderCV env)
      putStrLn "cv.pdf generated."
