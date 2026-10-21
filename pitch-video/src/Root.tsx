import React from 'react';
import {Composition, Folder} from 'remotion';
import {CRIPumpsPitch,PressureScene,ConnectionScene,OverviewScene,OrdersScene,ScheduleScene,MaterialsScene,CapacityScene,ReleaseScene,ProofScene,ValueScene,CloseScene} from './Pitch';
import timing from './timing.json';

export const RemotionRoot = () => <>
  <Composition id="CRIPumpsPitch" component={CRIPumpsPitch} width={1920} height={1080} fps={30} durationInFrames={timing.totalFrames}/>
  <Folder name="Scenes">
    <Composition id="SchedulingPressure" component={PressureScene} width={1920} height={1080} fps={30} durationInFrames={timing.scenes[0].frames}/>
    <Composition id="ConnectedPlanning" component={ConnectionScene} width={1920} height={1080} fps={30} durationInFrames={timing.scenes[1].frames}/>
    <Composition id="ShipmentOverview" component={OverviewScene} width={1920} height={1080} fps={30} durationInFrames={timing.scenes[2].frames}/>
    <Composition id="OrderDrilldown" component={OrdersScene} width={1920} height={1080} fps={30} durationInFrames={timing.scenes[3].frames}/>
    <Composition id="MachineSchedule" component={ScheduleScene} width={1920} height={1080} fps={30} durationInFrames={timing.scenes[4].frames}/>
    <Composition id="MaterialDependencies" component={MaterialsScene} width={1920} height={1080} fps={30} durationInFrames={timing.scenes[5].frames}/>
    <Composition id="CapacityEvidence" component={CapacityScene} width={1920} height={1080} fps={30} durationInFrames={timing.scenes[6].frames}/>
    <Composition id="ReleaseReview" component={ReleaseScene} width={1920} height={1080} fps={30} durationInFrames={timing.scenes[7].frames}/>
    <Composition id="PlanResults" component={ProofScene} width={1920} height={1080} fps={30} durationInFrames={timing.scenes[8].frames}/>
    <Composition id="OperationalValue" component={ValueScene} width={1920} height={1080} fps={30} durationInFrames={timing.scenes[9].frames}/>
    <Composition id="PilotInvitation" component={CloseScene} width={1920} height={1080} fps={30} durationInFrames={timing.scenes[10].frames}/>
  </Folder>
</>;
